use std::{
    collections::VecDeque,
    io::Cursor,
    str::FromStr,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};

use cpal::{
    Device, DeviceId, FromSample, Sample, SampleFormat, SizedSample, Stream, SupportedStreamConfig,
    traits::{DeviceTrait, HostTrait, StreamTrait},
};
use ringbuf::{
    HeapCons, HeapProd, HeapRb,
    traits::{Consumer, Observer, Producer, Split},
};
use serde::Serialize;
use tokio::sync::{Notify, mpsc};

const PREFERRED_CAPTURE_RATE: u32 = 48_000;
const CAPTURE_RING_SECONDS: usize = 2;
const CONSUMER_CHUNK_SAMPLES: usize = 2048;

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AudioDeviceInfo {
    pub id: String,
    pub name: String,
    pub is_default: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AudioDeviceList {
    pub inputs: Vec<AudioDeviceInfo>,
    pub outputs: Vec<AudioDeviceInfo>,
}

pub enum CaptureMessage {
    Samples(Vec<f32>),
    Error(String),
}

pub struct CaptureStream {
    stream: Stream,
    pub receiver: CaptureReceiver,
    pub device_name: String,
    pub sample_rate: u32,
}

impl CaptureStream {
    pub fn into_parts(self) -> (Stream, CaptureReceiver, String, u32) {
        (
            self.stream,
            self.receiver,
            self.device_name,
            self.sample_rate,
        )
    }
}

pub struct CaptureReceiver {
    consumer: HeapCons<f32>,
    errors: mpsc::Receiver<String>,
    notify: Arc<Notify>,
}

impl CaptureReceiver {
    pub async fn recv(&mut self) -> Option<CaptureMessage> {
        loop {
            if let Ok(error) = self.errors.try_recv() {
                return Some(CaptureMessage::Error(error));
            }
            let available = self.consumer.occupied_len().min(CONSUMER_CHUNK_SAMPLES);
            if available > 0 {
                let mut samples = vec![0.0; available];
                let count = self.consumer.pop_slice(&mut samples);
                samples.truncate(count);
                return Some(CaptureMessage::Samples(samples));
            }
            tokio::select! {
                _ = self.notify.notified() => {},
                error = self.errors.recv() => return error.map(CaptureMessage::Error),
            }
        }
    }

    pub fn clear_samples(&mut self) {
        while self.consumer.try_pop().is_some() {}
    }
}

pub fn list_devices() -> Result<AudioDeviceList, String> {
    let host = cpal::default_host();
    let default_input = device_id(host.default_input_device()).ok();
    let default_output = device_id(host.default_output_device()).ok();
    let inputs = host
        .input_devices()
        .map_err(|error| format!("枚举输入设备失败：{error}"))?
        .filter_map(|device| device_info(device, default_input.as_deref()).ok())
        .collect();
    let outputs = host
        .output_devices()
        .map_err(|error| format!("枚举输出设备失败：{error}"))?
        .filter_map(|device| device_info(device, default_output.as_deref()).ok())
        .collect();
    Ok(AudioDeviceList { inputs, outputs })
}

pub fn open_capture(
    selected_id: Option<&str>,
    enabled: Arc<AtomicBool>,
    muted: Arc<AtomicBool>,
) -> Result<CaptureStream, String> {
    let host = cpal::default_host();
    let device = resolve_device(&host, selected_id, true)?;
    let device_name = device_name(&device);
    let supported = preferred_input_config(&device)?;
    let sample_rate = supported.sample_rate();
    let channels = usize::from(supported.channels());
    let format = supported.sample_format();
    let config = supported.config();
    let ring = HeapRb::<f32>::new(sample_rate as usize * CAPTURE_RING_SECONDS);
    let (producer, consumer) = ring.split();
    let notify = Arc::new(Notify::new());
    let (error_sender, error_receiver) = mpsc::channel(4);
    let error_callback = move |error| {
        let _ = error_sender.try_send(format!("音频输入流中断：{error}"));
    };

    let callback_context = InputCallbackContext {
        channels,
        producer,
        notify: notify.clone(),
        enabled,
        muted,
    };
    let stream = match format {
        SampleFormat::F32 => {
            build_input_stream::<f32, _>(&device, config, callback_context, error_callback)
        }
        SampleFormat::I16 => {
            build_input_stream::<i16, _>(&device, config, callback_context, error_callback)
        }
        SampleFormat::I32 => {
            build_input_stream::<i32, _>(&device, config, callback_context, error_callback)
        }
        SampleFormat::U16 => {
            build_input_stream::<u16, _>(&device, config, callback_context, error_callback)
        }
        SampleFormat::U8 => {
            build_input_stream::<u8, _>(&device, config, callback_context, error_callback)
        }
        _ => return Err(format!("暂不支持麦克风采样格式 {format}")),
    }
    .map_err(|error| format!("打开麦克风失败：{error}"))?;
    stream
        .play()
        .map_err(|error| format!("启动麦克风失败：{error}"))?;

    Ok(CaptureStream {
        stream,
        receiver: CaptureReceiver {
            consumer,
            errors: error_receiver,
            notify,
        },
        device_name,
        sample_rate,
    })
}

struct InputCallbackContext {
    channels: usize,
    producer: HeapProd<f32>,
    notify: Arc<Notify>,
    enabled: Arc<AtomicBool>,
    muted: Arc<AtomicBool>,
}

fn build_input_stream<T, E>(
    device: &Device,
    config: cpal::StreamConfig,
    context: InputCallbackContext,
    error_callback: E,
) -> Result<Stream, cpal::Error>
where
    T: SizedSample + Sample + Copy,
    f32: FromSample<T>,
    E: FnMut(cpal::Error) + Send + 'static,
{
    let InputCallbackContext {
        channels,
        mut producer,
        notify,
        enabled,
        muted,
    } = context;
    device.build_input_stream(
        config,
        move |data: &[T], _| {
            if !enabled.load(Ordering::Relaxed) || muted.load(Ordering::Relaxed) {
                return;
            }
            let channels = channels.max(1);
            let mut mono = [0.0_f32; CONSUMER_CHUNK_SAMPLES];
            let mut pushed_any = false;
            for interleaved in data.chunks(CONSUMER_CHUNK_SAMPLES * channels) {
                let mut frame_count = 0;
                for (index, frame) in interleaved.chunks_exact(channels).enumerate() {
                    let sum = frame.iter().copied().map(f32::from_sample).sum::<f32>();
                    mono[index] = (sum / channels as f32).clamp(-1.0, 1.0);
                    frame_count += 1;
                }
                pushed_any |= producer.push_slice(&mono[..frame_count]) > 0;
            }
            if pushed_any {
                notify.notify_one();
            }
        },
        error_callback,
        Some(Duration::from_secs(5)),
    )
}

fn preferred_input_config(device: &Device) -> Result<SupportedStreamConfig, String> {
    let ranges = device
        .supported_input_configs()
        .map_err(|error| format!("读取麦克风格式失败：{error}"))?
        .collect::<Vec<_>>();
    let preferred_format = |format: SampleFormat| match format {
        SampleFormat::F32 => 5,
        SampleFormat::I16 => 4,
        SampleFormat::I32 => 3,
        SampleFormat::U16 => 2,
        SampleFormat::U8 => 1,
        _ => 0,
    };
    let mut candidates = ranges
        .into_iter()
        .filter(|range| preferred_format(range.sample_format()) > 0)
        .map(|range| {
            let supports_48k = range.min_sample_rate() <= PREFERRED_CAPTURE_RATE
                && range.max_sample_rate() >= PREFERRED_CAPTURE_RATE;
            let rate = if supports_48k {
                PREFERRED_CAPTURE_RATE
            } else {
                range.max_sample_rate().min(96_000)
            };
            let score = (
                supports_48k,
                std::cmp::Reverse(range.channels()),
                preferred_format(range.sample_format()),
            );
            (score, range.with_sample_rate(rate))
        })
        .collect::<Vec<_>>();
    candidates.sort_by_key(|(score, _)| *score);
    candidates
        .pop()
        .map(|(_, config)| config)
        .or_else(|| device.default_input_config().ok())
        .ok_or_else(|| "麦克风没有可用的 PCM 采样格式".to_owned())
}

fn resolve_device(
    host: &cpal::Host,
    selected_id: Option<&str>,
    input: bool,
) -> Result<Device, String> {
    if let Some(id) = selected_id {
        let id = DeviceId::from_str(id).map_err(|error| format!("音频设备 ID 无效：{error}"))?;
        if let Some(device) = host.device_by_id(&id) {
            let supported = if input {
                device.supports_input()
            } else {
                device.supports_output()
            };
            if supported {
                return Ok(device);
            }
        }
        return Err("已选择的音频设备不存在或已断开，请重新选择".to_owned());
    }
    if input {
        host.default_input_device()
            .ok_or_else(|| "系统没有默认麦克风".to_owned())
    } else {
        host.default_output_device()
            .ok_or_else(|| "系统没有默认播放设备".to_owned())
    }
}

fn device_info(device: Device, default_id: Option<&str>) -> Result<AudioDeviceInfo, String> {
    let id = device_id(Some(device.clone()))?;
    Ok(AudioDeviceInfo {
        is_default: default_id == Some(id.as_str()),
        id,
        name: device_name(&device),
    })
}

fn device_id(device: Option<Device>) -> Result<String, String> {
    device
        .ok_or_else(|| "audio device unavailable".to_owned())?
        .id()
        .map(|id| id.to_string())
        .map_err(|error| error.to_string())
}

fn device_name(device: &Device) -> String {
    device
        .description()
        .map(|description| description.name().to_owned())
        .unwrap_or_else(|_| "未知音频设备".to_owned())
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct VadDecision {
    pub started: bool,
    pub ended: bool,
}

pub struct VadDetector {
    noise_floor: f32,
    recording: bool,
    start_ms: f32,
    silent_ms: f32,
    utterance_ms: f32,
}

impl VadDetector {
    const START_THRESHOLD: f32 = 0.018;
    const END_THRESHOLD: f32 = 0.012;
    const MIN_START_THRESHOLD: f32 = 0.0015;
    const MAX_START_THRESHOLD: f32 = 0.03;
    const NOISE_RATIO: f32 = 4.0;
    const START_HOLD_MS: f32 = 96.0;
    const SILENCE_MS: f32 = 1100.0;
    const MAX_UTTERANCE_MS: f32 = 15_000.0;

    pub fn new() -> Self {
        Self {
            noise_floor: 0.0,
            recording: false,
            start_ms: 0.0,
            silent_ms: 0.0,
            utterance_ms: 0.0,
        }
    }

    pub fn feed(&mut self, samples: &[f32], sample_rate: u32) -> VadDecision {
        if samples.is_empty() || sample_rate == 0 {
            return VadDecision::default();
        }
        let rms = rms(samples);
        let frame_ms = samples.len() as f32 * 1000.0 / sample_rate as f32;
        if !self.recording {
            let threshold = self.threshold();
            if rms > threshold {
                self.start_ms += frame_ms;
            } else {
                self.start_ms = 0.0;
                self.track_noise(rms);
            }
            if self.start_ms >= Self::START_HOLD_MS {
                self.recording = true;
                self.silent_ms = 0.0;
                self.utterance_ms = 0.0;
                return VadDecision {
                    started: true,
                    ended: false,
                };
            }
            return VadDecision::default();
        }

        self.utterance_ms += frame_ms;
        let end_threshold = self.threshold() * (Self::END_THRESHOLD / Self::START_THRESHOLD);
        if rms < end_threshold {
            self.silent_ms += frame_ms;
        } else {
            self.silent_ms = 0.0;
        }
        if self.silent_ms >= Self::SILENCE_MS || self.utterance_ms >= Self::MAX_UTTERANCE_MS {
            self.reset_utterance();
            return VadDecision {
                started: false,
                ended: true,
            };
        }
        VadDecision::default()
    }

    pub fn threshold(&self) -> f32 {
        if self.noise_floor <= 0.0 {
            return Self::START_THRESHOLD;
        }
        (self.noise_floor * Self::NOISE_RATIO)
            .clamp(Self::MIN_START_THRESHOLD, Self::MAX_START_THRESHOLD)
    }

    fn track_noise(&mut self, rms: f32) {
        if self.noise_floor <= 0.0 {
            self.noise_floor = rms.max(0.00001);
            return;
        }
        let alpha = if rms < self.noise_floor {
            0.15
        } else if rms > self.threshold() * 1.2 {
            0.002
        } else {
            0.05
        };
        self.noise_floor = (1.0 - alpha) * self.noise_floor + alpha * rms;
    }

    pub fn reset_utterance(&mut self) {
        self.recording = false;
        self.start_ms = 0.0;
        self.silent_ms = 0.0;
        self.utterance_ms = 0.0;
    }
}

pub struct UtteranceBuffer {
    pre_roll: VecDeque<f32>,
    samples: Vec<f32>,
    recording: bool,
    pre_roll_samples: usize,
}

impl UtteranceBuffer {
    pub fn new(sample_rate: u32) -> Self {
        Self {
            pre_roll: VecDeque::new(),
            samples: Vec::new(),
            recording: false,
            pre_roll_samples: (sample_rate as usize * 300) / 1000,
        }
    }

    pub fn push(&mut self, samples: &[f32], decision: VadDecision) -> Option<Vec<f32>> {
        if decision.started {
            self.recording = true;
            self.samples.extend(self.pre_roll.drain(..));
        }
        if self.recording {
            self.samples.extend_from_slice(samples);
        } else {
            self.pre_roll.extend(samples.iter().copied());
            while self.pre_roll.len() > self.pre_roll_samples {
                self.pre_roll.pop_front();
            }
        }
        if decision.ended && self.recording {
            self.recording = false;
            self.pre_roll.clear();
            return Some(std::mem::take(&mut self.samples));
        }
        None
    }

    pub fn reset(&mut self) {
        self.pre_roll.clear();
        self.samples.clear();
        self.recording = false;
    }
}

pub fn rms(samples: &[f32]) -> f32 {
    if samples.is_empty() {
        return 0.0;
    }
    (samples
        .iter()
        .map(|sample| f64::from(*sample) * f64::from(*sample))
        .sum::<f64>()
        / samples.len() as f64)
        .sqrt() as f32
}

pub fn encode_wav(samples: &[f32], sample_rate: u32) -> Result<Vec<u8>, String> {
    let mut cursor = Cursor::new(Vec::new());
    {
        let spec = hound::WavSpec {
            channels: 1,
            sample_rate,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let mut writer = hound::WavWriter::new(&mut cursor, spec)
            .map_err(|error| format!("创建 WAV 失败：{error}"))?;
        for sample in samples {
            let pcm = (sample.clamp(-1.0, 1.0) * f32::from(i16::MAX)) as i16;
            writer
                .write_sample(pcm)
                .map_err(|error| format!("写入 WAV 失败：{error}"))?;
        }
        writer
            .finalize()
            .map_err(|error| format!("完成 WAV 失败：{error}"))?;
    }
    Ok(cursor.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(level: f32, milliseconds: usize, sample_rate: u32) -> Vec<f32> {
        vec![level; sample_rate as usize * milliseconds / 1000]
    }

    #[test]
    fn vad_is_duration_based_across_callback_sizes() {
        for frame_ms in [10, 20, 64] {
            let mut vad = VadDetector::new();
            for _ in 0..20 {
                vad.feed(&frame(0.0005, frame_ms, 48_000), 48_000);
            }
            let mut started = false;
            for _ in 0..20 {
                started |= vad.feed(&frame(0.02, frame_ms, 48_000), 48_000).started;
                if started {
                    break;
                }
            }
            assert!(started, "VAD did not start for {frame_ms} ms frames");
        }
    }

    #[test]
    fn vad_ends_after_sustained_silence() {
        let mut vad = VadDetector::new();
        for _ in 0..8 {
            vad.feed(&frame(0.03, 20, 16_000), 16_000);
        }
        let mut ended = false;
        for _ in 0..60 {
            ended |= vad.feed(&frame(0.0001, 20, 16_000), 16_000).ended;
        }
        assert!(ended);
    }

    #[test]
    fn wav_encoder_writes_a_valid_header() {
        let wav = encode_wav(&[0.0; 1600], 16_000).expect("encode");
        assert_eq!(&wav[..4], b"RIFF");
        assert_eq!(&wav[8..12], b"WAVE");
    }

    #[test]
    fn two_minutes_of_silence_keeps_pre_roll_memory_bounded() {
        let sample_rate = 16_000;
        let silence = frame(0.0, 20, sample_rate);
        let mut vad = VadDetector::new();
        let mut utterance = UtteranceBuffer::new(sample_rate);

        for _ in 0..6_000 {
            let decision = vad.feed(&silence, sample_rate);
            assert!(utterance.push(&silence, decision).is_none());
        }

        assert_eq!(utterance.pre_roll.len(), sample_rate as usize * 300 / 1000);
        assert!(utterance.samples.is_empty());
        assert!(!utterance.recording);
    }

    #[tokio::test]
    async fn capture_receiver_drains_the_spsc_ring() {
        let ring = HeapRb::<f32>::new(8);
        let (mut producer, consumer) = ring.split();
        let notify = Arc::new(Notify::new());
        let (_error_sender, errors) = mpsc::channel(1);
        let mut receiver = CaptureReceiver {
            consumer,
            errors,
            notify: notify.clone(),
        };
        assert_eq!(producer.push_slice(&[0.1, 0.2, 0.3]), 3);
        notify.notify_one();
        let Some(CaptureMessage::Samples(samples)) = receiver.recv().await else {
            panic!("expected samples");
        };
        assert_eq!(samples, vec![0.1, 0.2, 0.3]);
    }

    #[tokio::test]
    async fn clearing_stale_samples_preserves_device_errors() {
        let ring = HeapRb::<f32>::new(8);
        let (mut producer, consumer) = ring.split();
        let notify = Arc::new(Notify::new());
        let (error_sender, errors) = mpsc::channel(1);
        let mut receiver = CaptureReceiver {
            consumer,
            errors,
            notify,
        };
        assert_eq!(producer.push_slice(&[0.1, 0.2]), 2);
        error_sender
            .try_send("device removed".to_owned())
            .expect("queue device error");

        receiver.clear_samples();

        let Some(CaptureMessage::Error(message)) = receiver.recv().await else {
            panic!("expected preserved device error");
        };
        assert_eq!(message, "device removed");
    }
}
