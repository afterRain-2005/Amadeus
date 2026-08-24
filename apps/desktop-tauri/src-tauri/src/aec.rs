use std::{sync::Arc, time::Instant};

pub const AEC_SAMPLE_RATE: u32 = 16_000;

const DEFAULT_FILTER_LEN_MS: f32 = 120.0;
const DEFAULT_ALIGN_DELAY_MS: f32 = 80.0;
const DEFAULT_CONVERGENCE_MS: f32 = 1_200.0;
const DEFAULT_MU: f32 = 0.5;
const DEFAULT_NLP_THRESHOLD: f32 = 0.4;
const DEFAULT_NLP_GAIN: f32 = 0.6;
const SUBBLOCK_SAMPLES: usize = 128;

#[derive(Clone)]
pub struct PlaybackReference {
    samples: Arc<[f32]>,
    started_at: Instant,
}

impl PlaybackReference {
    pub fn new(samples: Vec<f32>) -> Self {
        Self {
            samples: samples.into(),
            started_at: Instant::now(),
        }
    }

    fn window(&self, length: usize, end_delay_samples: usize) -> Option<&[f32]> {
        let elapsed_samples =
            (self.started_at.elapsed().as_secs_f64() * f64::from(AEC_SAMPLE_RATE)) as usize;
        let end = elapsed_samples
            .saturating_sub(end_delay_samples)
            .min(self.samples.len());
        (end >= length).then(|| &self.samples[end - length..end])
    }
}

pub struct EchoCanceller {
    coefficients: Vec<f32>,
    mu: f32,
    align_delay_samples: usize,
    nlp_threshold: f32,
    nlp_gain: f32,
    convergence_ms: f32,
    processed_ms: f32,
    erle_ema: f32,
}

impl Default for EchoCanceller {
    fn default() -> Self {
        Self::new(
            DEFAULT_FILTER_LEN_MS,
            DEFAULT_MU,
            DEFAULT_ALIGN_DELAY_MS,
            DEFAULT_NLP_THRESHOLD,
            DEFAULT_NLP_GAIN,
            DEFAULT_CONVERGENCE_MS,
        )
    }
}

impl EchoCanceller {
    fn new(
        filter_len_ms: f32,
        mu: f32,
        align_delay_ms: f32,
        nlp_threshold: f32,
        nlp_gain: f32,
        convergence_ms: f32,
    ) -> Self {
        let filter_len = ((filter_len_ms * AEC_SAMPLE_RATE as f32 / 1000.0) as usize).max(64);
        Self {
            coefficients: vec![0.0; filter_len],
            mu: mu.clamp(0.05, 1.5),
            align_delay_samples: (align_delay_ms * AEC_SAMPLE_RATE as f32 / 1000.0).max(0.0)
                as usize,
            nlp_threshold: nlp_threshold.clamp(0.1, 1.0),
            nlp_gain: nlp_gain.clamp(0.0, 0.95),
            convergence_ms: convergence_ms.max(1.0),
            processed_ms: 0.0,
            erle_ema: 0.0,
        }
    }

    pub fn process(
        &mut self,
        microphone: &[f32],
        microphone_rate: u32,
        reference: &PlaybackReference,
    ) -> Option<Vec<f32>> {
        let microphone = resample_mono(microphone, microphone_rate, AEC_SAMPLE_RATE);
        let needed = microphone
            .len()
            .saturating_add(self.coefficients.len().saturating_sub(1));
        let history = reference.window(needed, self.align_delay_samples)?;
        let cleaned = self.process_aligned(&microphone, history);
        self.converged().then_some(cleaned)
    }

    pub fn reset(&mut self) {
        self.coefficients.fill(0.0);
        self.processed_ms = 0.0;
        self.erle_ema = 0.0;
    }

    fn converged(&self) -> bool {
        self.processed_ms >= self.convergence_ms && self.erle_ema > 6.0
    }

    fn process_aligned(&mut self, microphone: &[f32], reference: &[f32]) -> Vec<f32> {
        let sample_count = microphone.len();
        let filter_len = self.coefficients.len();
        if sample_count == 0 || reference.len() < sample_count + filter_len - 1 {
            return microphone.to_vec();
        }

        let mut output = vec![0.0; sample_count];
        let microphone_power = dot(microphone, microphone);
        let mut error_power = 0.0;
        for start in (0..sample_count).step_by(SUBBLOCK_SAMPLES) {
            let block_len = SUBBLOCK_SAMPLES.min(sample_count - start);
            let reference_block = &reference[start..start + block_len + filter_len - 1];
            let microphone_block = &microphone[start..start + block_len];
            let mut errors = vec![0.0; block_len];

            for index in 0..block_len {
                let estimate = dot(
                    &reference_block[index..index + filter_len],
                    &self.coefficients,
                );
                let error = microphone_block[index] - estimate;
                errors[index] = error;
                output[start + index] = error;
                error_power += error * error;
            }

            let reference_power = dot(reference_block, reference_block);
            let normalization =
                filter_len as f32 * reference_power / reference_block.len() as f32 + 1.0e-10;
            let scale = self.mu / normalization;
            for coefficient in 0..filter_len {
                let mut gradient = 0.0;
                for index in 0..block_len {
                    gradient += reference_block[index + coefficient] * errors[index];
                }
                self.coefficients[coefficient] += scale * gradient;
            }
        }

        let mut echo_power = 0.0;
        for (microphone, error) in microphone.iter().zip(&output) {
            let estimate = microphone - error;
            echo_power += estimate * estimate;
        }
        let echo_ratio = echo_power / (microphone_power + 1.0e-10);
        if self.nlp_gain > 0.0 && echo_ratio > self.nlp_threshold {
            let gain = (1.0 - self.nlp_gain).max(0.05);
            for sample in &mut output {
                *sample *= gain;
            }
        }

        let erle = 10.0 * ((microphone_power + 1.0e-10) / (error_power + 1.0e-10)).log10();
        self.erle_ema = 0.9 * self.erle_ema + 0.1 * erle;
        self.processed_ms += sample_count as f32 * 1000.0 / AEC_SAMPLE_RATE as f32;
        output
    }
}

pub fn resample_mono(samples: &[f32], source_rate: u32, target_rate: u32) -> Vec<f32> {
    if samples.is_empty() || source_rate == 0 || target_rate == 0 {
        return Vec::new();
    }
    if source_rate == target_rate {
        return samples.to_vec();
    }
    let output_len = ((samples.len() as f64 * f64::from(target_rate) / f64::from(source_rate))
        .round() as usize)
        .max(1);
    let ratio = source_rate as f64 / target_rate as f64;
    let mut output = Vec::with_capacity(output_len);
    for index in 0..output_len {
        let position = index as f64 * ratio;
        let left = (position.floor() as usize).min(samples.len() - 1);
        let right = (left + 1).min(samples.len() - 1);
        let fraction = (position - left as f64) as f32;
        output.push(samples[left] + (samples[right] - samples[left]) * fraction);
    }
    output
}

fn dot(left: &[f32], right: &[f32]) -> f32 {
    left.iter()
        .zip(right)
        .map(|(left, right)| left * right)
        .sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn noise(length: usize) -> Vec<f32> {
        noise_with_seed(length, 0x1234_5678)
    }

    fn noise_with_seed(length: usize, mut state: u32) -> Vec<f32> {
        (0..length)
            .map(|_| {
                state = state.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
                (state as f32 / u32::MAX as f32 - 0.5) * 0.2
            })
            .collect()
    }

    #[test]
    fn nlms_suppresses_a_synthetic_delayed_echo() {
        const BLOCK: usize = 1024;
        let filter_len = 1920;
        let delay = 960;
        let reference = noise(AEC_SAMPLE_RATE as usize * 7 + filter_len);
        let mut microphone = vec![0.0; reference.len()];
        for index in delay..microphone.len() {
            microphone[index] = reference[index - delay] * 0.7;
        }
        let mut aec = EchoCanceller::new(120.0, 0.5, 0.0, 0.4, 0.0, 1_200.0);
        let mut tail_erle = 0.0;
        for start in (0..microphone.len() - BLOCK - filter_len).step_by(BLOCK) {
            let mic = &microphone[start + filter_len - 1..start + filter_len - 1 + BLOCK];
            let history = &reference[start..start + BLOCK + filter_len - 1];
            let cleaned = aec.process_aligned(mic, history);
            tail_erle =
                10.0 * ((dot(mic, mic) + 1.0e-10) / (dot(&cleaned, &cleaned) + 1.0e-10)).log10();
        }
        assert!(
            tail_erle > 18.0,
            "echo reduction was only {tail_erle:.1} dB"
        );
        assert!(aec.converged());
    }

    #[test]
    fn resampling_preserves_duration_and_endpoints() {
        let input = vec![0.0, 0.25, 0.5, 0.75];
        let output = resample_mono(&input, 4, 8);
        assert_eq!(output.len(), 8);
        assert_eq!(output[0], 0.0);
        assert_eq!(*output.last().expect("output"), 0.75);
    }

    #[test]
    fn nlms_keeps_double_talk_energy_after_echo_training() {
        const BLOCK: usize = 1024;
        let filter_len = 1920;
        let delay = 800;
        let reference = noise(AEC_SAMPLE_RATE as usize * 7 + filter_len);
        let user = noise_with_seed(reference.len(), 0x8765_4321)
            .into_iter()
            .map(|sample| sample * 0.2)
            .collect::<Vec<_>>();
        let mut microphone = vec![0.0; reference.len()];
        for index in delay..microphone.len() {
            microphone[index] = reference[index - delay] * 0.7;
            if index > microphone.len() / 2 {
                microphone[index] += user[index];
            }
        }
        let mut aec = EchoCanceller::new(120.0, 0.5, 0.0, 0.4, 0.0, 1_200.0);
        let mut kept_ratios = Vec::new();
        for start in (0..microphone.len() - BLOCK - filter_len).step_by(BLOCK) {
            let offset = start + filter_len - 1;
            let mic = &microphone[offset..offset + BLOCK];
            let history = &reference[start..start + BLOCK + filter_len - 1];
            let cleaned = aec.process_aligned(mic, history);
            if offset > microphone.len() / 2 {
                let speech = &user[offset..offset + BLOCK];
                kept_ratios.push(dot(&cleaned, &cleaned) / (dot(speech, speech) + 1.0e-10));
            }
        }
        let ratio = kept_ratios.iter().sum::<f32>() / kept_ratios.len() as f32;
        assert!(
            (0.3..3.0).contains(&ratio),
            "double-talk energy ratio was {ratio:.2}"
        );
    }
}
