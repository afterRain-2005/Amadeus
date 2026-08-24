use std::{
    collections::VecDeque,
    io::Cursor,
    str::FromStr,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};

use amadeus_core::{CoreEvent, SessionId, VoicePhase};
use base64::{Engine, engine::general_purpose::STANDARD as BASE64};
use cpal::Stream;
use futures_util::StreamExt;
use reqwest::{Url, header::CONTENT_LENGTH};
use rodio::{
    Decoder, DeviceSinkBuilder, Player, Source,
    buffer::SamplesBuffer,
    cpal::traits::{DeviceTrait as RodioDeviceTrait, HostTrait as RodioHostTrait},
};
use tauri::{AppHandle, Emitter};
use tokio::time::{sleep, timeout};
use tokio_util::sync::CancellationToken;

use crate::{
    aec::{AEC_SAMPLE_RATE, EchoCanceller, PlaybackReference, resample_mono},
    audio::{
        CaptureMessage, CaptureReceiver, UtteranceBuffer, VadDetector, encode_wav, open_capture,
        rms,
    },
    audio_settings::{AudioCredentials, AudioSettingsStore, TtsCredentials, TtsProvider},
    chat::{self, ChatState},
    sapi,
};

const CORE_EVENT: &str = "core-event";
const MAX_AUDIO_DOWNLOAD_BYTES: usize = 25 * 1024 * 1024;
const MIN_UTTERANCE_SAMPLES: usize = 1600;
const CAPTURE_STALL_TIMEOUT: Duration = Duration::from_secs(4);
const MAX_RECONNECT_DELAY: Duration = Duration::from_secs(5);
const BARGE_IN_THRESHOLD_RATIO: f32 = 2.5;
const BARGE_IN_HOLD_MS: f32 = 96.0;
const BARGE_IN_SILENCE_MS: f32 = 770.0;
const BARGE_IN_PRE_ROLL_MS: usize = 400;
const MAX_BARGE_IN_UTTERANCE_MS: f32 = 15_000.0;
const MAX_DECODED_AUDIO_SECONDS: usize = 180;
const MAX_DECODED_AUDIO_SAMPLES: usize = 16_000_000;

type PlaybackReferenceSlot = Arc<Mutex<Option<PlaybackReference>>>;

struct ActiveVoice {
    session_id: SessionId,
    cancel: CancellationToken,
    muted: Arc<AtomicBool>,
    capture_enabled: Arc<AtomicBool>,
    screen_share: Arc<AtomicBool>,
    stream: Option<Stream>,
}

struct VoiceInner {
    active: Mutex<Option<ActiveVoice>>,
}

#[derive(Clone)]
pub struct VoiceState {
    client: reqwest::Client,
    settings: AudioSettingsStore,
    inner: Arc<VoiceInner>,
}

impl VoiceState {
    pub fn new(settings: AudioSettingsStore) -> Result<Self, String> {
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(90))
            .redirect(reqwest::redirect::Policy::none())
            .user_agent("Amadeus-Next/0.1")
            .build()
            .map_err(|error| format!("初始化音频网络客户端失败：{error}"))?;
        Ok(Self {
            client,
            settings,
            inner: Arc::new(VoiceInner {
                active: Mutex::new(None),
            }),
        })
    }

    fn install(&self, active: ActiveVoice) -> Result<(), String> {
        let mut current = self
            .inner
            .active
            .lock()
            .map_err(|_| "voice state lock was poisoned".to_owned())?;
        if current.is_some() {
            return Err("语音通话已经在运行".to_owned());
        }
        *current = Some(active);
        Ok(())
    }

    fn finish(&self, session_id: &SessionId) {
        if let Ok(mut active) = self.inner.active.lock()
            && active
                .as_ref()
                .is_some_and(|current| &current.session_id == session_id)
        {
            *active = None;
        }
    }

    pub fn cancel(&self) -> Result<bool, String> {
        let mut active = self
            .inner
            .active
            .lock()
            .map_err(|_| "voice state lock was poisoned".to_owned())?;
        if let Some(active) = active.as_mut() {
            active.capture_enabled.store(false, Ordering::Release);
            active.cancel.cancel();
            active.stream.take();
            return Ok(true);
        }
        Ok(false)
    }

    fn replace_stream(&self, session_id: &SessionId, stream: Stream) -> Result<(), String> {
        let mut active = self
            .inner
            .active
            .lock()
            .map_err(|_| "voice state lock was poisoned".to_owned())?;
        let current = active
            .as_mut()
            .filter(|current| &current.session_id == session_id)
            .ok_or_else(|| "语音通话已经结束".to_owned())?;
        if current.cancel.is_cancelled() {
            return Err("语音通话已经取消".to_owned());
        }
        current.stream = Some(stream);
        Ok(())
    }

    fn drop_stream(&self, session_id: &SessionId) -> Result<(), String> {
        let mut active = self
            .inner
            .active
            .lock()
            .map_err(|_| "voice state lock was poisoned".to_owned())?;
        let current = active
            .as_mut()
            .filter(|current| &current.session_id == session_id)
            .ok_or_else(|| "语音通话已经结束".to_owned())?;
        current.stream.take();
        Ok(())
    }

    pub fn toggle_mute(&self, app: &AppHandle) -> Result<bool, String> {
        let active = self
            .inner
            .active
            .lock()
            .map_err(|_| "voice state lock was poisoned".to_owned())?;
        let active = active
            .as_ref()
            .ok_or_else(|| "语音通话尚未启动".to_owned())?;
        let muted = !active.muted.load(Ordering::Acquire);
        active.muted.store(muted, Ordering::Release);
        emit(
            app,
            CoreEvent::VoiceMutedChanged {
                session_id: active.session_id.clone(),
                muted,
            },
        );
        Ok(muted)
    }

    pub fn toggle_screen_share(&self, app: &AppHandle) -> Result<bool, String> {
        let active = self
            .inner
            .active
            .lock()
            .map_err(|_| "voice state lock was poisoned".to_owned())?;
        let active = active
            .as_ref()
            .ok_or_else(|| "语音通话尚未启动".to_owned())?;
        let enabled = !active.screen_share.load(Ordering::Acquire);
        active.screen_share.store(enabled, Ordering::Release);
        emit(
            app,
            CoreEvent::VoiceScreenShareChanged {
                session_id: active.session_id.clone(),
                enabled,
            },
        );
        Ok(enabled)
    }
}

pub fn start_voice_call(app: AppHandle, voice: VoiceState, chat: ChatState) -> Result<(), String> {
    let credentials = voice.settings.credentials()?;
    let muted = Arc::new(AtomicBool::new(false));
    let capture_enabled = Arc::new(AtomicBool::new(true));
    let screen_share = Arc::new(AtomicBool::new(false));
    let capture = open_capture(
        credentials.input_device_id.as_deref(),
        capture_enabled.clone(),
        muted.clone(),
    )?;
    let (stream, receiver, device_name, sample_rate) = capture.into_parts();
    let session_id = SessionId::new();
    voice.install(ActiveVoice {
        session_id: session_id.clone(),
        cancel: CancellationToken::new(),
        muted: muted.clone(),
        capture_enabled: capture_enabled.clone(),
        screen_share: screen_share.clone(),
        stream: Some(stream),
    })?;
    let cancel = {
        let active = voice
            .inner
            .active
            .lock()
            .map_err(|_| "voice state lock was poisoned".to_owned())?;
        active
            .as_ref()
            .expect("voice session was just installed")
            .cancel
            .clone()
    };

    emit_phase(&app, &session_id, VoicePhase::Listening);
    emit(
        &app,
        CoreEvent::VoiceDeviceChanged {
            session_id: session_id.clone(),
            input_name: device_name,
            sample_rate,
        },
    );
    emit(
        &app,
        CoreEvent::VoiceScreenShareChanged {
            session_id: session_id.clone(),
            enabled: false,
        },
    );
    let task_voice = voice.clone();
    let task_app = app.clone();
    tauri::async_runtime::spawn(async move {
        let result = run_voice_loop(
            &task_app,
            &task_voice,
            &chat,
            credentials,
            receiver,
            sample_rate,
            &session_id,
            &cancel,
            &capture_enabled,
            &muted,
            &screen_share,
        )
        .await;
        if let Err(message) = result
            && !cancel.is_cancelled()
        {
            emit(
                &task_app,
                CoreEvent::Error {
                    session_id: Some(session_id.clone()),
                    code: "voice_failed".to_owned(),
                    message,
                },
            );
        }
        capture_enabled.store(false, Ordering::Release);
        task_voice.finish(&session_id);
        emit_phase(&task_app, &session_id, VoicePhase::Ended);
    });
    Ok(())
}

#[allow(clippy::too_many_arguments)]
async fn run_voice_loop(
    app: &AppHandle,
    voice: &VoiceState,
    chat_state: &ChatState,
    credentials: AudioCredentials,
    mut receiver: CaptureReceiver,
    mut sample_rate: u32,
    session_id: &SessionId,
    cancel: &CancellationToken,
    capture_enabled: &Arc<AtomicBool>,
    muted: &Arc<AtomicBool>,
    screen_share: &Arc<AtomicBool>,
) -> Result<(), String> {
    let mut vad = VadDetector::new();
    let mut utterance = UtteranceBuffer::new(sample_rate);
    let mut echo_canceller = EchoCanceller::default();
    let mut last_level = Instant::now() - Duration::from_secs(1);
    'voice: loop {
        let message = if capture_enabled.load(Ordering::Acquire) && !muted.load(Ordering::Acquire) {
            tokio::select! {
                _ = cancel.cancelled() => return Ok(()),
                result = timeout(CAPTURE_STALL_TIMEOUT, receiver.recv()) => {
                    match result {
                        Ok(message) => Ok(message),
                        Err(_) => Err("麦克风长时间没有返回音频".to_owned()),
                    }
                }
            }
        } else {
            tokio::select! {
                _ = cancel.cancelled() => return Ok(()),
                message = receiver.recv() => Ok(message),
            }
        };
        let samples = match message {
            Ok(Some(CaptureMessage::Samples(samples))) => samples,
            Ok(Some(CaptureMessage::Error(message))) => {
                let recovered = recover_capture(
                    app,
                    voice,
                    &credentials,
                    session_id,
                    cancel,
                    capture_enabled,
                    muted,
                    message,
                )
                .await?;
                receiver = recovered.receiver;
                sample_rate = recovered.sample_rate;
                vad = VadDetector::new();
                utterance = UtteranceBuffer::new(sample_rate);
                echo_canceller.reset();
                last_level = Instant::now() - Duration::from_secs(1);
                continue;
            }
            Ok(None) => {
                let recovered = recover_capture(
                    app,
                    voice,
                    &credentials,
                    session_id,
                    cancel,
                    capture_enabled,
                    muted,
                    "麦克风输入流意外结束".to_owned(),
                )
                .await?;
                receiver = recovered.receiver;
                sample_rate = recovered.sample_rate;
                vad = VadDetector::new();
                utterance = UtteranceBuffer::new(sample_rate);
                echo_canceller.reset();
                last_level = Instant::now() - Duration::from_secs(1);
                continue;
            }
            Err(message) => {
                if !capture_enabled.load(Ordering::Acquire) || muted.load(Ordering::Acquire) {
                    continue;
                }
                let recovered = recover_capture(
                    app,
                    voice,
                    &credentials,
                    session_id,
                    cancel,
                    capture_enabled,
                    muted,
                    message,
                )
                .await?;
                receiver = recovered.receiver;
                sample_rate = recovered.sample_rate;
                vad = VadDetector::new();
                utterance = UtteranceBuffer::new(sample_rate);
                echo_canceller.reset();
                last_level = Instant::now() - Duration::from_secs(1);
                continue;
            }
        };
        emit_voice_level(app, session_id, &samples, vad.threshold(), &mut last_level);
        let decision = vad.feed(&samples, sample_rate);
        if decision.started {
            emit_phase(app, session_id, VoicePhase::Recording);
        }
        let Some(samples) = utterance.push(&samples, decision) else {
            continue;
        };
        if samples.len() < MIN_UTTERANCE_SAMPLES {
            emit_phase(app, session_id, VoicePhase::Listening);
            continue;
        }

        let mut pending_samples = Some(samples);
        while let Some(turn_samples) = pending_samples.take() {
            let outcome = process_turn_with_barge_in(
                app,
                voice,
                chat_state,
                &credentials,
                turn_samples,
                sample_rate,
                session_id,
                cancel,
                &mut receiver,
                muted,
                vad.threshold(),
                &mut last_level,
                &mut echo_canceller,
                screen_share,
            )
            .await;
            receiver.clear_samples();
            vad.reset_utterance();
            utterance.reset();
            match outcome {
                TurnMonitorOutcome::SessionCancelled => return Ok(()),
                TurnMonitorOutcome::Interrupted(samples) => {
                    if samples.len() >= MIN_UTTERANCE_SAMPLES {
                        pending_samples = Some(samples);
                    } else {
                        emit_phase(app, session_id, VoicePhase::Listening);
                    }
                }
                TurnMonitorOutcome::Completed(Ok(())) => {
                    emit_phase(app, session_id, VoicePhase::Listening);
                }
                TurnMonitorOutcome::Completed(Err(message)) => {
                    emit(
                        app,
                        CoreEvent::Error {
                            session_id: Some(session_id.clone()),
                            code: "voice_turn_failed".to_owned(),
                            message,
                        },
                    );
                    emit_phase(app, session_id, VoicePhase::Listening);
                }
                TurnMonitorOutcome::CaptureFailed(message) => {
                    let recovered = recover_capture(
                        app,
                        voice,
                        &credentials,
                        session_id,
                        cancel,
                        capture_enabled,
                        muted,
                        message,
                    )
                    .await?;
                    receiver = recovered.receiver;
                    sample_rate = recovered.sample_rate;
                    vad = VadDetector::new();
                    utterance = UtteranceBuffer::new(sample_rate);
                    echo_canceller.reset();
                    last_level = Instant::now() - Duration::from_secs(1);
                    continue 'voice;
                }
            }
        }
    }
}

enum TurnMonitorOutcome {
    Completed(Result<(), String>),
    Interrupted(Vec<f32>),
    CaptureFailed(String),
    SessionCancelled,
}

#[allow(clippy::too_many_arguments)]
async fn process_turn_with_barge_in(
    app: &AppHandle,
    voice: &VoiceState,
    chat_state: &ChatState,
    credentials: &AudioCredentials,
    samples: Vec<f32>,
    sample_rate: u32,
    session_id: &SessionId,
    session_cancel: &CancellationToken,
    receiver: &mut CaptureReceiver,
    muted: &Arc<AtomicBool>,
    vad_threshold: f32,
    last_level: &mut Instant,
    echo_canceller: &mut EchoCanceller,
    screen_share: &Arc<AtomicBool>,
) -> TurnMonitorOutcome {
    let turn_cancel = session_cancel.child_token();
    let playback_active = Arc::new(AtomicBool::new(false));
    let playback_reference = Arc::new(Mutex::new(None));
    let turn = process_voice_turn(
        app,
        voice,
        chat_state,
        credentials,
        samples,
        sample_rate,
        session_id,
        &turn_cancel,
        playback_active.clone(),
        playback_reference.clone(),
        screen_share,
    );
    tokio::pin!(turn);
    let mut turn_result = None;
    let mut interruption = InterruptionDetector::new(sample_rate);

    loop {
        if turn_result.is_some() && !interruption.is_recording() {
            return TurnMonitorOutcome::Completed(
                turn_result.take().expect("turn result was checked above"),
            );
        }
        let watchdog = !muted.load(Ordering::Acquire);
        tokio::select! {
            _ = session_cancel.cancelled() => {
                turn_cancel.cancel();
                let _ = chat_state.cancel();
                if turn_result.is_none() {
                    let _ = timeout(Duration::from_secs(2), &mut turn).await;
                }
                return TurnMonitorOutcome::SessionCancelled;
            }
            result = &mut turn, if turn_result.is_none() => {
                turn_result = Some(result);
            }
            message = receive_capture(receiver, watchdog) => {
                let samples = match message {
                    Ok(Some(CaptureMessage::Samples(samples))) => samples,
                    Ok(Some(CaptureMessage::Error(message))) => {
                        turn_cancel.cancel();
                        let _ = chat_state.cancel();
                        if turn_result.is_none() {
                            let _ = timeout(Duration::from_secs(2), &mut turn).await;
                        }
                        return TurnMonitorOutcome::CaptureFailed(message);
                    }
                    Ok(None) => {
                        turn_cancel.cancel();
                        let _ = chat_state.cancel();
                        if turn_result.is_none() {
                            let _ = timeout(Duration::from_secs(2), &mut turn).await;
                        }
                        return TurnMonitorOutcome::CaptureFailed(
                            "麦克风输入流意外结束".to_owned(),
                        );
                    }
                    Err(message) => {
                        turn_cancel.cancel();
                        let _ = chat_state.cancel();
                        if turn_result.is_none() {
                            let _ = timeout(Duration::from_secs(2), &mut turn).await;
                        }
                        return TurnMonitorOutcome::CaptureFailed(message);
                    }
                };
                emit_voice_level(app, session_id, &samples, vad_threshold, last_level);
                if interruption.is_recording()
                    || credentials.barge_in_enabled && playback_active.load(Ordering::Acquire)
                {
                    let reference = playback_reference
                        .lock()
                        .ok()
                        .and_then(|reference| reference.clone());
                    match interruption.feed(
                        &samples,
                        vad_threshold,
                        echo_canceller,
                        reference.as_ref(),
                    ) {
                        Some(BargeInEvent::Triggered) => {
                            turn_cancel.cancel();
                            emit_phase(app, session_id, VoicePhase::Recording);
                        }
                        Some(BargeInEvent::Complete(samples)) => {
                            if turn_result.is_none() {
                                let _ = timeout(Duration::from_secs(2), &mut turn).await;
                            }
                            return TurnMonitorOutcome::Interrupted(samples);
                        }
                        None => {}
                    }
                } else {
                    interruption.reset();
                }
            }
        }
    }
}

async fn receive_capture(
    receiver: &mut CaptureReceiver,
    watchdog: bool,
) -> Result<Option<CaptureMessage>, String> {
    if watchdog {
        timeout(CAPTURE_STALL_TIMEOUT, receiver.recv())
            .await
            .map_err(|_| "麦克风长时间没有返回音频".to_owned())
    } else {
        Ok(receiver.recv().await)
    }
}

fn emit_voice_level(
    app: &AppHandle,
    session_id: &SessionId,
    samples: &[f32],
    threshold: f32,
    last_level: &mut Instant,
) {
    if last_level.elapsed() < Duration::from_millis(50) {
        return;
    }
    let normalized = ((rms(samples) / threshold).clamp(0.0, 1.0) * 1000.0) as u16;
    emit(
        app,
        CoreEvent::VoiceLevel {
            session_id: session_id.clone(),
            level: normalized,
        },
    );
    *last_level = Instant::now();
}

enum BargeInEvent {
    Triggered,
    Complete(Vec<f32>),
}

struct InterruptionDetector {
    microphone_rate: u32,
    fallback: BargeInDetector,
    aec_vad: VadDetector,
    aec_utterance: UtteranceBuffer,
    aec_recording: bool,
}

impl InterruptionDetector {
    fn new(microphone_rate: u32) -> Self {
        Self {
            microphone_rate,
            fallback: BargeInDetector::new(microphone_rate),
            aec_vad: VadDetector::new(),
            aec_utterance: UtteranceBuffer::new(AEC_SAMPLE_RATE),
            aec_recording: false,
        }
    }

    fn is_recording(&self) -> bool {
        self.aec_recording || self.fallback.is_recording()
    }

    fn feed(
        &mut self,
        samples: &[f32],
        vad_threshold: f32,
        echo_canceller: &mut EchoCanceller,
        reference: Option<&PlaybackReference>,
    ) -> Option<BargeInEvent> {
        if self.fallback.is_recording() {
            return self.fallback.feed(samples, vad_threshold);
        }

        let microphone_16k = resample_mono(samples, self.microphone_rate, AEC_SAMPLE_RATE);
        let vad_input = if self.aec_recording {
            microphone_16k
        } else if let Some(cleaned) = reference
            .and_then(|reference| echo_canceller.process(samples, self.microphone_rate, reference))
        {
            self.fallback.reset();
            cleaned
        } else {
            self.aec_vad.reset_utterance();
            self.aec_utterance.reset();
            return self.fallback.feed(samples, vad_threshold);
        };

        let decision = self.aec_vad.feed(&vad_input, AEC_SAMPLE_RATE);
        let completed = self.aec_utterance.push(&vad_input, decision);
        if decision.started {
            self.aec_recording = true;
            return Some(BargeInEvent::Triggered);
        }
        if let Some(samples) = completed {
            self.aec_recording = false;
            return Some(BargeInEvent::Complete(samples));
        }
        None
    }

    fn reset(&mut self) {
        self.fallback.reset();
        self.aec_vad.reset_utterance();
        self.aec_utterance.reset();
        self.aec_recording = false;
    }
}

struct BargeInDetector {
    sample_rate: u32,
    pre_roll: VecDeque<f32>,
    samples: Vec<f32>,
    high_ms: f32,
    silent_ms: f32,
    utterance_ms: f32,
    recording: bool,
}

impl BargeInDetector {
    fn new(sample_rate: u32) -> Self {
        Self {
            sample_rate,
            pre_roll: VecDeque::new(),
            samples: Vec::new(),
            high_ms: 0.0,
            silent_ms: 0.0,
            utterance_ms: 0.0,
            recording: false,
        }
    }

    fn is_recording(&self) -> bool {
        self.recording
    }

    fn feed(&mut self, samples: &[f32], vad_threshold: f32) -> Option<BargeInEvent> {
        if samples.is_empty() || self.sample_rate == 0 {
            return None;
        }
        let frame_ms = samples.len() as f32 * 1000.0 / self.sample_rate as f32;
        let level = rms(samples);
        if !self.recording {
            self.pre_roll.extend(samples.iter().copied());
            let pre_roll_samples = self.sample_rate as usize * BARGE_IN_PRE_ROLL_MS / 1000;
            while self.pre_roll.len() > pre_roll_samples {
                self.pre_roll.pop_front();
            }
            if level >= vad_threshold * BARGE_IN_THRESHOLD_RATIO {
                self.high_ms += frame_ms;
            } else {
                self.high_ms = 0.0;
            }
            if self.high_ms >= BARGE_IN_HOLD_MS {
                self.recording = true;
                self.samples.extend(self.pre_roll.drain(..));
                self.utterance_ms = self.samples.len() as f32 * 1000.0 / self.sample_rate as f32;
                self.silent_ms = 0.0;
                return Some(BargeInEvent::Triggered);
            }
            return None;
        }

        self.samples.extend_from_slice(samples);
        self.utterance_ms += frame_ms;
        if level < vad_threshold {
            self.silent_ms += frame_ms;
        } else {
            self.silent_ms = 0.0;
        }
        if self.silent_ms >= BARGE_IN_SILENCE_MS || self.utterance_ms >= MAX_BARGE_IN_UTTERANCE_MS {
            self.recording = false;
            self.pre_roll.clear();
            self.high_ms = 0.0;
            self.silent_ms = 0.0;
            self.utterance_ms = 0.0;
            return Some(BargeInEvent::Complete(std::mem::take(&mut self.samples)));
        }
        None
    }

    fn reset(&mut self) {
        self.pre_roll.clear();
        self.samples.clear();
        self.high_ms = 0.0;
        self.silent_ms = 0.0;
        self.utterance_ms = 0.0;
        self.recording = false;
    }
}

struct RecoveredCapture {
    receiver: CaptureReceiver,
    sample_rate: u32,
}

#[derive(Debug, Default)]
struct ReconnectSchedule {
    attempt: u16,
}

impl ReconnectSchedule {
    fn next_attempt(&mut self) -> (u16, Duration) {
        self.attempt = self.attempt.saturating_add(1);
        if self.attempt == 1 {
            return (self.attempt, Duration::ZERO);
        }
        let exponent = u32::from(self.attempt.saturating_sub(2)).min(5);
        let delay = Duration::from_millis(250_u64.saturating_mul(1_u64 << exponent));
        (self.attempt, delay.min(MAX_RECONNECT_DELAY))
    }
}

#[allow(clippy::too_many_arguments)]
async fn recover_capture(
    app: &AppHandle,
    voice: &VoiceState,
    credentials: &AudioCredentials,
    session_id: &SessionId,
    cancel: &CancellationToken,
    capture_enabled: &Arc<AtomicBool>,
    muted: &Arc<AtomicBool>,
    cause: String,
) -> Result<RecoveredCapture, String> {
    voice.drop_stream(session_id)?;
    emit_phase(app, session_id, VoicePhase::Reconnecting);
    emit(
        app,
        CoreEvent::VoiceLevel {
            session_id: session_id.clone(),
            level: 0,
        },
    );

    let mut schedule = ReconnectSchedule::default();
    let mut last_error = cause;
    loop {
        let (attempt, delay) = schedule.next_attempt();
        emit(
            app,
            CoreEvent::VoiceDeviceRecovery {
                session_id: session_id.clone(),
                attempt,
                retry_in_ms: delay.as_millis().try_into().unwrap_or(u64::MAX),
                message: last_error.clone(),
            },
        );
        if !delay.is_zero() {
            tokio::select! {
                _ = cancel.cancelled() => return Err("语音通话已经取消".to_owned()),
                _ = sleep(delay) => {}
            }
        }
        if cancel.is_cancelled() {
            return Err("语音通话已经取消".to_owned());
        }

        match open_capture(
            credentials.input_device_id.as_deref(),
            capture_enabled.clone(),
            muted.clone(),
        ) {
            Ok(capture) => {
                let (stream, receiver, device_name, sample_rate) = capture.into_parts();
                if cancel.is_cancelled() {
                    return Err("语音通话已经取消".to_owned());
                }
                voice.replace_stream(session_id, stream)?;
                emit(
                    app,
                    CoreEvent::VoiceDeviceChanged {
                        session_id: session_id.clone(),
                        input_name: device_name,
                        sample_rate,
                    },
                );
                emit_phase(app, session_id, VoicePhase::Listening);
                return Ok(RecoveredCapture {
                    receiver,
                    sample_rate,
                });
            }
            Err(error) => last_error = error,
        }
    }
}

#[allow(clippy::too_many_arguments)]
async fn process_voice_turn(
    app: &AppHandle,
    voice: &VoiceState,
    chat_state: &ChatState,
    credentials: &AudioCredentials,
    samples: Vec<f32>,
    sample_rate: u32,
    session_id: &SessionId,
    cancel: &CancellationToken,
    playback_active: Arc<AtomicBool>,
    playback_reference: PlaybackReferenceSlot,
    screen_share: &Arc<AtomicBool>,
) -> Result<(), String> {
    emit_phase(app, session_id, VoicePhase::Transcribing);
    let wav = encode_wav(&samples, sample_rate)?;
    let transcript = transcribe(&voice.client, credentials, wav, cancel).await?;
    if transcript.trim().is_empty() {
        return Err("语音识别返回了空文本".to_owned());
    }
    emit(
        app,
        CoreEvent::VoiceTranscript {
            session_id: session_id.clone(),
            text: transcript.clone(),
        },
    );

    emit_phase(app, session_id, VoicePhase::Thinking);
    let image_data_url = if screen_share.load(Ordering::Acquire) {
        emit(
            app,
            CoreEvent::VoiceSubtitle {
                session_id: session_id.clone(),
                text: "正在读取当前主屏幕…".to_owned(),
            },
        );
        match crate::screen::capture_primary_jpeg().await {
            Ok(image) if screen_share.load(Ordering::Acquire) => Some(image),
            Ok(_) => None,
            Err(message) => {
                emit(
                    app,
                    CoreEvent::Error {
                        session_id: Some(session_id.clone()),
                        code: "voice_screen_capture_failed".to_owned(),
                        message,
                    },
                );
                None
            }
        }
    } else {
        None
    };
    let (delta_sender, mut delta_receiver) = tokio::sync::mpsc::unbounded_channel();
    let task_app = app.clone();
    let task_chat = chat_state.clone();
    let mut chat_task = tauri::async_runtime::spawn(async move {
        chat::run_chat_turn_with_image(
            &task_app,
            &task_chat,
            transcript,
            image_data_url,
            Some(delta_sender),
        )
        .await
    });
    let mut segmenter = SpeechSegmenter::default();
    let mut queued = VecDeque::new();
    let mut chat_result = None;
    let mut stream_closed = false;

    loop {
        while let Ok(delta) = delta_receiver.try_recv() {
            queued.extend(segmenter.push(&delta));
        }
        if let Some(segment) = queued.pop_front() {
            emit(
                app,
                CoreEvent::VoiceSubtitle {
                    session_id: session_id.clone(),
                    text: clean_tts_text(&segment),
                },
            );
            speak_reply(
                app,
                voice,
                credentials,
                &segment,
                session_id,
                cancel,
                playback_active.clone(),
                playback_reference.clone(),
            )
            .await?;
            continue;
        }
        if stream_closed {
            if let Some(tail) = segmenter.finish() {
                queued.push_back(tail);
                continue;
            }
            break;
        }
        if !playback_active.load(Ordering::Acquire) {
            emit_phase(app, session_id, VoicePhase::Thinking);
        }
        tokio::select! {
            _ = cancel.cancelled() => {
                let _ = chat_state.cancel();
                let _ = timeout(Duration::from_secs(2), &mut chat_task).await;
                return Ok(());
            }
            delta = delta_receiver.recv() => {
                match delta {
                    Some(delta) => queued.extend(segmenter.push(&delta)),
                    None => stream_closed = true,
                }
            }
            result = &mut chat_task, if chat_result.is_none() => {
                chat_result = Some(
                    result.map_err(|error| format!("模型流任务失败：{error}"))?
                );
            }
        }
    }
    let reply = match chat_result {
        Some(result) => result?,
        None => chat_task
            .await
            .map_err(|error| format!("模型流任务失败：{error}"))??,
    };
    if reply.trim().is_empty() && !cancel.is_cancelled() {
        return Err("模型返回了空回复".to_owned());
    }
    Ok(())
}

#[derive(Default)]
struct SpeechSegmenter {
    pending: String,
}

impl SpeechSegmenter {
    fn push(&mut self, delta: &str) -> Vec<String> {
        self.pending.push_str(delta);
        let mut segments = Vec::new();
        while let Some(end) = speech_boundary(&self.pending) {
            let segment = self.pending.drain(..end).collect::<String>();
            let segment = segment.trim().to_owned();
            if !segment.is_empty() {
                segments.push(segment);
            }
        }
        segments
    }

    fn finish(&mut self) -> Option<String> {
        let tail = std::mem::take(&mut self.pending).trim().to_owned();
        (!tail.is_empty()).then_some(tail)
    }
}

fn speech_boundary(text: &str) -> Option<usize> {
    const MIN_SEGMENT_CHARS: usize = 6;
    const MAX_SEGMENT_CHARS: usize = 90;
    let mut count = 0;
    let mut soft_boundary = None;
    for (index, character) in text.char_indices() {
        count += 1;
        let end = index + character.len_utf8();
        if matches!(character, '，' | ',' | '；' | ';' | '、') && count >= 24 {
            soft_boundary = Some(end);
        }
        if matches!(character, '。' | '！' | '？' | '!' | '?' | '\n') && count >= MIN_SEGMENT_CHARS
        {
            return Some(end);
        }
        if count >= MAX_SEGMENT_CHARS {
            return Some(soft_boundary.unwrap_or(end));
        }
    }
    None
}

#[allow(clippy::too_many_arguments)]
async fn speak_reply(
    app: &AppHandle,
    voice: &VoiceState,
    credentials: &AudioCredentials,
    reply: &str,
    session_id: &SessionId,
    cancel: &CancellationToken,
    playback_active: Arc<AtomicBool>,
    playback_reference: PlaybackReferenceSlot,
) -> Result<(), String> {
    let text = clean_tts_text(reply);
    if text.is_empty() || cancel.is_cancelled() {
        return Ok(());
    }
    if matches!(&credentials.tts, TtsProvider::Disabled) {
        return Ok(());
    }
    let _playback_activity = PlaybackActivity::begin(playback_active);
    emit_phase(app, session_id, VoicePhase::Speaking);
    match &credentials.tts {
        TtsProvider::Disabled => Ok(()),
        TtsProvider::Sapi => sapi::speak(text, cancel.clone()).await,
        TtsProvider::Aliyun {
            credentials: tts,
            sapi_fallback,
        } => {
            let cloud_result = play_cloud_tts(
                app,
                session_id,
                &voice.client,
                tts,
                &text,
                credentials.output_device_id.clone(),
                cancel,
                playback_reference,
            )
            .await;
            match cloud_result {
                Ok(()) => Ok(()),
                Err(cloud_error) if *sapi_fallback && !cancel.is_cancelled() => {
                    sapi::speak(text, cancel.clone())
                        .await
                        .map_err(|sapi_error| {
                            format!("{cloud_error}；Windows 系统语音降级也失败：{sapi_error}")
                        })
                }
                Err(error) => Err(error),
            }
        }
    }
}

struct PlaybackActivity {
    active: Arc<AtomicBool>,
}

impl PlaybackActivity {
    fn begin(active: Arc<AtomicBool>) -> Self {
        active.store(true, Ordering::Release);
        Self { active }
    }
}

impl Drop for PlaybackActivity {
    fn drop(&mut self) {
        self.active.store(false, Ordering::Release);
    }
}

#[allow(clippy::too_many_arguments)]
async fn play_cloud_tts(
    app: &AppHandle,
    session_id: &SessionId,
    client: &reqwest::Client,
    credentials: &TtsCredentials,
    text: &str,
    output_device_id: Option<String>,
    cancel: &CancellationToken,
    playback_reference: PlaybackReferenceSlot,
) -> Result<(), String> {
    let audio = synthesize(client, credentials, text, cancel).await?;
    let playback_cancel = cancel.clone();
    let playback_app = app.clone();
    let playback_session = session_id.clone();
    tokio::task::spawn_blocking(move || {
        play_audio(
            &playback_app,
            &playback_session,
            audio,
            output_device_id.as_deref(),
            &playback_cancel,
            &playback_reference,
        )
    })
    .await
    .map_err(|error| format!("音频播放线程失败：{error}"))?
}

async fn transcribe(
    client: &reqwest::Client,
    credentials: &AudioCredentials,
    wav: Vec<u8>,
    cancel: &CancellationToken,
) -> Result<String, String> {
    let body = serde_json::json!({
        "model": credentials.asr_model,
        "messages": [{
            "role": "user",
            "content": [{
                "type": "input_audio",
                "input_audio": {"data": BASE64.encode(wav), "format": "wav"}
            }]
        }],
        "stream": false
    });
    let mut request = client.post(credentials.asr_url.clone()).json(&body);
    if let Some(api_key) = credentials.asr_api_key.as_ref() {
        request = request.bearer_auth(api_key.as_str());
    }
    let response = tokio::select! {
        _ = cancel.cancelled() => return Ok(String::new()),
        response = request.send() => response.map_err(|error| format!("ASR 连接失败：{error}"))?,
    };
    let status = response.status();
    if !status.is_success() {
        let detail = response
            .text()
            .await
            .unwrap_or_default()
            .chars()
            .take(500)
            .collect::<String>();
        return Err(format!("ASR 服务返回 HTTP {status}：{detail}"));
    }
    let payload: serde_json::Value = response
        .json()
        .await
        .map_err(|error| format!("ASR 响应不是有效 JSON：{error}"))?;
    payload
        .pointer("/choices/0/message/content")
        .and_then(serde_json::Value::as_str)
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .map(ToOwned::to_owned)
        .ok_or_else(|| "ASR 响应中没有转写文本".to_owned())
}

async fn synthesize(
    client: &reqwest::Client,
    credentials: &TtsCredentials,
    text: &str,
    cancel: &CancellationToken,
) -> Result<Vec<u8>, String> {
    let body = serde_json::json!({
        "model": credentials.model,
        "input": {
            "text": text,
            "voice": credentials.voice_id,
            "language_type": "Chinese"
        }
    });
    let request = client
        .post(credentials.url.clone())
        .bearer_auth(credentials.api_key.as_str())
        .json(&body);
    let response = tokio::select! {
        _ = cancel.cancelled() => return Ok(Vec::new()),
        response = request.send() => response.map_err(|error| format!("TTS 连接失败：{error}"))?,
    };
    let status = response.status();
    if !status.is_success() {
        let detail = response
            .text()
            .await
            .unwrap_or_default()
            .chars()
            .take(500)
            .collect::<String>();
        return Err(format!("TTS 服务返回 HTTP {status}：{detail}"));
    }
    let payload: serde_json::Value = response
        .json()
        .await
        .map_err(|error| format!("TTS 响应不是有效 JSON：{error}"))?;
    let url = payload
        .pointer("/output/audio/url")
        .and_then(serde_json::Value::as_str)
        .or_else(|| {
            payload
                .pointer("/output/audio_url")
                .and_then(serde_json::Value::as_str)
        })
        .ok_or_else(|| "TTS 响应中没有音频地址".to_owned())?;
    let url = validate_aliyun_audio_url(url)?;
    download_audio(client, url, cancel).await
}

async fn download_audio(
    client: &reqwest::Client,
    url: Url,
    cancel: &CancellationToken,
) -> Result<Vec<u8>, String> {
    let response = tokio::select! {
        _ = cancel.cancelled() => return Ok(Vec::new()),
        response = client.get(url).send() => response.map_err(|error| format!("下载 TTS 音频失败：{error}"))?,
    };
    if !response.status().is_success() {
        return Err(format!("TTS 音频下载返回 HTTP {}", response.status()));
    }
    if response
        .headers()
        .get(CONTENT_LENGTH)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse::<usize>().ok())
        .is_some_and(|length| length > MAX_AUDIO_DOWNLOAD_BYTES)
    {
        return Err("TTS 音频超过 25 MiB 安全限制".to_owned());
    }
    let mut stream = response.bytes_stream();
    let mut audio = Vec::new();
    loop {
        let next = tokio::select! {
            _ = cancel.cancelled() => return Ok(Vec::new()),
            next = stream.next() => next,
        };
        let Some(chunk) = next else {
            break;
        };
        let chunk = chunk.map_err(|error| format!("读取 TTS 音频失败：{error}"))?;
        if audio.len().saturating_add(chunk.len()) > MAX_AUDIO_DOWNLOAD_BYTES {
            return Err("TTS 音频超过 25 MiB 安全限制".to_owned());
        }
        audio.extend_from_slice(&chunk);
    }
    if audio.is_empty() {
        return Err("TTS 返回了空音频".to_owned());
    }
    Ok(audio)
}

fn play_audio(
    app: &AppHandle,
    session_id: &SessionId,
    bytes: Vec<u8>,
    output_device_id: Option<&str>,
    cancel: &CancellationToken,
    playback_reference: &PlaybackReferenceSlot,
) -> Result<(), String> {
    if bytes.is_empty() || cancel.is_cancelled() {
        return Ok(());
    }
    let host = rodio::cpal::default_host();
    let device = if let Some(id) = output_device_id {
        let id = rodio::cpal::DeviceId::from_str(id)
            .map_err(|error| format!("播放设备 ID 无效：{error}"))?;
        host.device_by_id(&id)
            .filter(|device| device.supports_output())
            .ok_or_else(|| "已选择的播放设备不存在或已断开".to_owned())?
    } else {
        host.default_output_device()
            .ok_or_else(|| "系统没有默认播放设备".to_owned())?
    };
    let sink = DeviceSinkBuilder::from_device(device)
        .map_err(|error| format!("读取播放设备配置失败：{error}"))?
        .open_sink_or_fallback()
        .map_err(|error| format!("打开播放设备失败：{error}"))?;
    let player = Player::connect_new(sink.mixer());
    let decoder = Decoder::try_from(Cursor::new(bytes))
        .map_err(|error| format!("解码 TTS 音频失败：{error}"))?;
    let channels = decoder.channels();
    let sample_rate = decoder.sample_rate();
    let duration_limit =
        sample_rate.get() as usize * channels.get() as usize * MAX_DECODED_AUDIO_SECONDS;
    let sample_limit = duration_limit.min(MAX_DECODED_AUDIO_SAMPLES);
    let decoded = decoder
        .take(sample_limit.saturating_add(1))
        .collect::<Vec<_>>();
    if decoded.is_empty() {
        return Err("TTS 返回了空音频".to_owned());
    }
    if decoded.len() > sample_limit {
        return Err("解码后的 TTS 音频超过安全时长或内存限制".to_owned());
    }
    if cancel.is_cancelled() {
        return Ok(());
    }
    let mono = downmix_to_mono(&decoded, channels.get() as usize);
    let reference = resample_mono(&mono, sample_rate.get(), AEC_SAMPLE_RATE);
    let _reference_guard = PlaybackReferenceGuard::install(playback_reference, reference);
    player.append(SamplesBuffer::new(channels, sample_rate, decoded));
    let playback_started = Instant::now();
    let level_window_frames = (sample_rate.get() as usize / 20).max(1);
    while !player.empty() && !cancel.is_cancelled() {
        let frame = (playback_started.elapsed().as_secs_f64() * sample_rate.get() as f64) as usize;
        let start = frame.min(mono.len().saturating_sub(1));
        let end = start.saturating_add(level_window_frames).min(mono.len());
        let level = if end > start {
            (rms(&mono[start..end]) * 5.0).clamp(0.0, 1.0)
        } else {
            0.0
        };
        emit(
            app,
            CoreEvent::VoicePlaybackLevel {
                session_id: session_id.clone(),
                level: (level * 1000.0) as u16,
            },
        );
        std::thread::sleep(Duration::from_millis(20));
    }
    if cancel.is_cancelled() {
        player.stop();
    }
    emit(
        app,
        CoreEvent::VoicePlaybackLevel {
            session_id: session_id.clone(),
            level: 0,
        },
    );
    Ok(())
}

fn downmix_to_mono(interleaved: &[f32], channels: usize) -> Vec<f32> {
    let channels = channels.max(1);
    interleaved
        .chunks_exact(channels)
        .map(|frame| frame.iter().sum::<f32>() / channels as f32)
        .collect()
}

struct PlaybackReferenceGuard {
    slot: PlaybackReferenceSlot,
}

impl PlaybackReferenceGuard {
    fn install(slot: &PlaybackReferenceSlot, samples: Vec<f32>) -> Option<Self> {
        if samples.is_empty() {
            return None;
        }
        let mut reference = slot.lock().ok()?;
        *reference = Some(PlaybackReference::new(samples));
        drop(reference);
        Some(Self { slot: slot.clone() })
    }
}

impl Drop for PlaybackReferenceGuard {
    fn drop(&mut self) {
        if let Ok(mut reference) = self.slot.lock() {
            *reference = None;
        }
    }
}

fn clean_tts_text(text: &str) -> String {
    text.replace("[emotion:smile]", "")
        .replace("[emotion:sad]", "")
        .replace("[emotion:angry]", "")
        .replace("```", "")
        .chars()
        .take(1500)
        .collect::<String>()
        .trim()
        .to_owned()
}

fn validate_aliyun_audio_url(value: &str) -> Result<Url, String> {
    let url = Url::parse(value).map_err(|_| "TTS 返回了无效音频地址".to_owned())?;
    let host = url
        .host_str()
        .ok_or_else(|| "TTS 音频地址缺少主机名".to_owned())?;
    if url.scheme() != "https" || !(host == "aliyuncs.com" || host.ends_with(".aliyuncs.com")) {
        return Err("TTS 音频地址未通过 HTTPS/阿里云域名校验".to_owned());
    }
    Ok(url)
}

fn emit_phase(app: &AppHandle, session_id: &SessionId, phase: VoicePhase) {
    emit(
        app,
        CoreEvent::VoicePhaseChanged {
            session_id: session_id.clone(),
            phase,
        },
    );
}

fn emit(app: &AppHandle, event: CoreEvent) {
    let _ = app.emit(CORE_EVENT, event);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_accepts_https_aliyun_audio_urls() {
        assert!(
            validate_aliyun_audio_url("https://bucket.oss-cn-hangzhou.aliyuncs.com/a.mp3").is_ok()
        );
        assert!(
            validate_aliyun_audio_url("http://bucket.oss-cn-hangzhou.aliyuncs.com/a.mp3").is_err()
        );
        assert!(validate_aliyun_audio_url("https://aliyuncs.com.evil.example/a.mp3").is_err());
    }

    #[test]
    fn cleans_control_markup_before_tts() {
        assert_eq!(clean_tts_text("[emotion:smile]你好```"), "你好");
    }

    #[test]
    fn reconnect_schedule_retries_immediately_then_caps_backoff() {
        let mut schedule = ReconnectSchedule::default();
        let delays = (0..9)
            .map(|_| schedule.next_attempt().1)
            .collect::<Vec<_>>();
        assert_eq!(
            delays,
            [
                Duration::ZERO,
                Duration::from_millis(250),
                Duration::from_millis(500),
                Duration::from_secs(1),
                Duration::from_secs(2),
                Duration::from_secs(4),
                Duration::from_secs(5),
                Duration::from_secs(5),
                Duration::from_secs(5),
            ]
        );
    }

    #[test]
    fn barge_in_requires_sustained_loud_speech_and_records_until_silence() {
        let mut detector = BargeInDetector::new(16_000);
        let threshold = 0.01;
        let loud = vec![threshold * 3.0; 1024];
        assert!(detector.feed(&loud, threshold).is_none());
        assert!(matches!(
            detector.feed(&loud, threshold),
            Some(BargeInEvent::Triggered)
        ));
        assert!(detector.is_recording());

        let silence = vec![0.0; 1024];
        let mut completed = None;
        for _ in 0..13 {
            if let Some(BargeInEvent::Complete(samples)) = detector.feed(&silence, threshold) {
                completed = Some(samples);
                break;
            }
        }
        let samples = completed.expect("770ms of silence should finish interrupted speech");
        assert!(samples.len() >= loud.len() * 2 + silence.len() * 12);
        assert!(!detector.is_recording());
    }

    #[test]
    fn barge_in_ignores_echo_below_the_guard_threshold() {
        let mut detector = BargeInDetector::new(48_000);
        let threshold = 0.01;
        let echo = vec![threshold * 2.4; 2048];
        for _ in 0..30 {
            assert!(detector.feed(&echo, threshold).is_none());
        }
        assert!(!detector.is_recording());
        assert!(detector.pre_roll.len() <= 48_000 * BARGE_IN_PRE_ROLL_MS / 1000);
    }

    #[test]
    fn playback_activity_is_cleared_when_the_turn_future_drops() {
        let active = Arc::new(AtomicBool::new(false));
        {
            let _guard = PlaybackActivity::begin(active.clone());
            assert!(active.load(Ordering::Acquire));
        }
        assert!(!active.load(Ordering::Acquire));
    }

    #[test]
    fn downmixes_interleaved_audio_without_changing_frame_count() {
        let stereo = [1.0, -1.0, 0.5, 0.25, -0.5, -0.25];
        assert_eq!(downmix_to_mono(&stereo, 2), [0.0, 0.375, -0.375]);
    }

    #[test]
    fn playback_reference_guard_never_leaks_across_turns() {
        let slot = Arc::new(Mutex::new(None));
        {
            let _guard = PlaybackReferenceGuard::install(&slot, vec![0.0; 320]);
            assert!(slot.lock().expect("slot").is_some());
        }
        assert!(slot.lock().expect("slot").is_none());
    }

    #[test]
    fn streaming_speech_waits_for_complete_sentences() {
        let mut segmenter = SpeechSegmenter::default();
        assert!(segmenter.push("这是一段还没说").is_empty());
        assert_eq!(
            segmenter.push("完的话。下一句也完成了！"),
            ["这是一段还没说完的话。", "下一句也完成了！"]
        );
        assert!(segmenter.finish().is_none());
    }

    #[test]
    fn streaming_speech_flushes_tail_and_bounds_long_segments() {
        let mut segmenter = SpeechSegmenter::default();
        let long = "很长的内容，".repeat(20);
        let segments = segmenter.push(&long);
        assert!(!segments.is_empty());
        assert!(segments.iter().all(|segment| segment.chars().count() <= 90));
        assert!(segmenter.finish().is_some());
    }
}
