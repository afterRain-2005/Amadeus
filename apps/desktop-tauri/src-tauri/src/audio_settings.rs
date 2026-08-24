use std::{fs, io, path::PathBuf};

use reqwest::Url;
use serde::{Deserialize, Serialize};
use zeroize::Zeroizing;

use crate::{
    config_io,
    settings::{endpoint_is_loopback, read_secret, update_secret, validate_endpoint},
};

const SETTINGS_FILE: &str = "audio.json";
const ASR_CREDENTIAL_TARGET: &str = "com.wweiyi.amadeus.next/asr-api-key";
const TTS_CREDENTIAL_TARGET: &str = "com.wweiyi.amadeus.next/tts-api-key";
const DEFAULT_ASR_ENDPOINT: &str = "https://api.xiaomimimo.com/v1";
const DEFAULT_ASR_MODEL: &str = "mimo-audio-v1";
const DEFAULT_TTS_MODEL: &str = "qwen3-tts-vc-2026-01-22";
const TTS_ENDPOINT: &str =
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation";

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SaveAudioSettings {
    pub input_device_id: Option<String>,
    pub output_device_id: Option<String>,
    pub asr_endpoint: String,
    pub asr_model: String,
    pub asr_api_key: Option<String>,
    pub barge_in_enabled: bool,
    pub tts_enabled: bool,
    pub tts_sapi_fallback: bool,
    pub tts_model: String,
    pub tts_voice_id: String,
    pub tts_api_key: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PublicAudioSettings {
    pub input_device_id: Option<String>,
    pub output_device_id: Option<String>,
    pub asr_endpoint: String,
    pub asr_model: String,
    pub has_asr_api_key: bool,
    pub barge_in_enabled: bool,
    pub tts_enabled: bool,
    pub tts_sapi_fallback: bool,
    pub tts_model: String,
    pub tts_voice_id: String,
    pub has_tts_api_key: bool,
    pub ready: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct AudioMetadata {
    input_device_id: Option<String>,
    output_device_id: Option<String>,
    asr_endpoint: String,
    asr_model: String,
    #[serde(default = "default_true")]
    barge_in_enabled: bool,
    tts_enabled: bool,
    #[serde(default = "default_true")]
    tts_sapi_fallback: bool,
    tts_model: String,
    tts_voice_id: String,
}

impl Default for AudioMetadata {
    fn default() -> Self {
        Self {
            input_device_id: None,
            output_device_id: None,
            asr_endpoint: DEFAULT_ASR_ENDPOINT.to_owned(),
            asr_model: DEFAULT_ASR_MODEL.to_owned(),
            barge_in_enabled: true,
            tts_enabled: true,
            tts_sapi_fallback: true,
            tts_model: DEFAULT_TTS_MODEL.to_owned(),
            tts_voice_id: String::new(),
        }
    }
}

pub struct AudioCredentials {
    pub input_device_id: Option<String>,
    pub output_device_id: Option<String>,
    pub asr_url: Url,
    pub asr_model: String,
    pub asr_api_key: Option<Zeroizing<String>>,
    pub barge_in_enabled: bool,
    pub tts: TtsProvider,
}

pub enum TtsProvider {
    Disabled,
    Sapi,
    Aliyun {
        credentials: TtsCredentials,
        sapi_fallback: bool,
    },
}

pub struct TtsCredentials {
    pub url: Url,
    pub model: String,
    pub voice_id: String,
    pub api_key: Zeroizing<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum TtsSelection {
    Disabled,
    Aliyun,
    Sapi,
    MissingApiKey,
    MissingVoiceId,
}

#[derive(Clone)]
pub struct AudioSettingsStore {
    path: PathBuf,
}

impl AudioSettingsStore {
    pub fn new(config_dir: PathBuf) -> Self {
        Self {
            path: config_dir.join(SETTINGS_FILE),
        }
    }

    pub fn public(&self) -> Result<PublicAudioSettings, String> {
        let metadata = self.load_metadata()?;
        let asr_endpoint = validate_endpoint(&metadata.asr_endpoint)?;
        validate_text("ASR 模型", &metadata.asr_model, 128, false)?;
        validate_text("TTS 模型", &metadata.tts_model, 128, false)?;
        validate_text("TTS 音色 ID", &metadata.tts_voice_id, 256, true)?;
        validate_device_id(metadata.input_device_id.as_deref())?;
        validate_device_id(metadata.output_device_id.as_deref())?;

        let has_asr_api_key = read_secret(ASR_CREDENTIAL_TARGET)?.is_some();
        let has_tts_api_key = read_secret(TTS_CREDENTIAL_TARGET)?.is_some();
        let asr_ready = has_asr_api_key || endpoint_is_loopback(&asr_endpoint);
        let cloud_tts_ready = has_tts_api_key && !metadata.tts_voice_id.trim().is_empty();
        let tts_ready = !metadata.tts_enabled
            || cloud_tts_ready
            || metadata.tts_sapi_fallback && sapi_available();
        Ok(PublicAudioSettings {
            input_device_id: metadata.input_device_id,
            output_device_id: metadata.output_device_id,
            asr_endpoint: metadata.asr_endpoint,
            asr_model: metadata.asr_model,
            has_asr_api_key,
            barge_in_enabled: metadata.barge_in_enabled,
            tts_enabled: metadata.tts_enabled,
            tts_sapi_fallback: metadata.tts_sapi_fallback,
            tts_model: metadata.tts_model,
            tts_voice_id: metadata.tts_voice_id,
            has_tts_api_key,
            ready: asr_ready && tts_ready,
        })
    }

    pub fn save(&self, input: SaveAudioSettings) -> Result<PublicAudioSettings, String> {
        let endpoint = validate_endpoint(&input.asr_endpoint)?;
        let asr_model = validate_text("ASR 模型", &input.asr_model, 128, false)?;
        let tts_model = validate_text("TTS 模型", &input.tts_model, 128, false)?;
        let tts_voice_id = validate_text("TTS 音色 ID", &input.tts_voice_id, 256, true)?;
        let input_device_id = normalize_device_id(input.input_device_id)?;
        let output_device_id = normalize_device_id(input.output_device_id)?;

        update_secret(ASR_CREDENTIAL_TARGET, input.asr_api_key)?;
        update_secret(TTS_CREDENTIAL_TARGET, input.tts_api_key)?;

        let metadata = AudioMetadata {
            input_device_id,
            output_device_id,
            asr_endpoint: endpoint.as_str().trim_end_matches('/').to_owned(),
            asr_model,
            barge_in_enabled: input.barge_in_enabled,
            tts_enabled: input.tts_enabled,
            tts_sapi_fallback: input.tts_sapi_fallback,
            tts_model,
            tts_voice_id,
        };
        let json = serde_json::to_vec_pretty(&metadata)
            .map_err(|error| format!("序列化音频设置失败：{error}"))?;
        config_io::write_bytes(&self.path, &json)
            .map_err(|error| format!("写入音频设置失败：{error}"))?;
        self.public()
    }

    pub fn credentials(&self) -> Result<AudioCredentials, String> {
        let metadata = self.load_metadata()?;
        let endpoint = validate_endpoint(&metadata.asr_endpoint)?;
        let asr_model = validate_text("ASR 模型", &metadata.asr_model, 128, false)?;
        let asr_api_key = read_secret(ASR_CREDENTIAL_TARGET)?;
        if asr_api_key.is_none() && !endpoint_is_loopback(&endpoint) {
            return Err("请先在语音设置中保存 ASR API Key".to_owned());
        }
        let asr_url = Url::parse(&format!(
            "{}/chat/completions",
            endpoint.as_str().trim_end_matches('/')
        ))
        .map_err(|error| format!("构造 ASR 地址失败：{error}"))?;

        let tts = if !metadata.tts_enabled {
            TtsProvider::Disabled
        } else {
            let api_key = read_secret(TTS_CREDENTIAL_TARGET)?;
            let has_api_key = api_key.is_some();
            let voice_id = validate_text("TTS 音色 ID", &metadata.tts_voice_id, 256, true)?;
            let sapi_fallback = metadata.tts_sapi_fallback && sapi_available();
            match select_tts_provider(
                metadata.tts_enabled,
                has_api_key,
                !voice_id.is_empty(),
                sapi_fallback,
            ) {
                TtsSelection::Aliyun => {
                    let api_key = api_key.expect("Aliyun selection requires an API key");
                    let model = validate_text("TTS 模型", &metadata.tts_model, 128, false)?;
                    TtsProvider::Aliyun {
                        credentials: TtsCredentials {
                            url: Url::parse(TTS_ENDPOINT)
                                .expect("constant TTS endpoint must be valid"),
                            model,
                            voice_id,
                            api_key,
                        },
                        sapi_fallback,
                    }
                }
                TtsSelection::Sapi => TtsProvider::Sapi,
                TtsSelection::MissingApiKey => {
                    return Err(
                        "请先保存阿里云 TTS API Key，或启用 Windows 系统语音降级".to_owned()
                    );
                }
                TtsSelection::MissingVoiceId => {
                    return Err(
                        "请先填写阿里云 TTS 音色 ID，或启用 Windows 系统语音降级".to_owned()
                    );
                }
                TtsSelection::Disabled => unreachable!("TTS is enabled in this branch"),
            }
        };

        Ok(AudioCredentials {
            input_device_id: metadata.input_device_id,
            output_device_id: metadata.output_device_id,
            asr_url,
            asr_model,
            asr_api_key,
            barge_in_enabled: metadata.barge_in_enabled,
            tts,
        })
    }

    fn load_metadata(&self) -> Result<AudioMetadata, String> {
        match fs::read(&self.path) {
            Ok(bytes) => serde_json::from_slice(&bytes)
                .map_err(|error| format!("音频设置文件已损坏：{error}")),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(AudioMetadata::default()),
            Err(error) => Err(format!("读取音频设置失败：{error}")),
        }
    }
}

const fn default_true() -> bool {
    true
}

const fn sapi_available() -> bool {
    cfg!(windows)
}

const fn select_tts_provider(
    enabled: bool,
    has_api_key: bool,
    has_voice_id: bool,
    sapi_fallback: bool,
) -> TtsSelection {
    if !enabled {
        TtsSelection::Disabled
    } else if has_api_key && has_voice_id {
        TtsSelection::Aliyun
    } else if sapi_fallback {
        TtsSelection::Sapi
    } else if !has_api_key {
        TtsSelection::MissingApiKey
    } else {
        TtsSelection::MissingVoiceId
    }
}

fn normalize_device_id(value: Option<String>) -> Result<Option<String>, String> {
    let value = value.map(|value| value.trim().to_owned());
    validate_device_id(value.as_deref())?;
    Ok(value.filter(|value| !value.is_empty()))
}

fn validate_device_id(value: Option<&str>) -> Result<(), String> {
    if let Some(value) = value
        && (value.len() > 2048 || value.chars().any(char::is_control))
    {
        return Err("音频设备 ID 无效".to_owned());
    }
    Ok(())
}

fn validate_text(
    label: &str,
    value: &str,
    max_len: usize,
    allow_empty: bool,
) -> Result<String, String> {
    let value = value.trim();
    if (!allow_empty && value.is_empty())
        || value.len() > max_len
        || value.chars().any(char::is_control)
    {
        return Err(format!("{label}格式无效"));
    }
    Ok(value.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_audio_metadata_enables_local_tts_fallback() {
        let metadata = AudioMetadata::default();
        assert!(metadata.tts_enabled);
        assert!(metadata.barge_in_enabled);
        assert!(metadata.tts_sapi_fallback);
        assert!(metadata.tts_voice_id.is_empty());
        assert_eq!(metadata.asr_model, DEFAULT_ASR_MODEL);
    }

    #[test]
    fn old_audio_metadata_enables_sapi_fallback_during_migration() {
        let metadata: AudioMetadata = serde_json::from_str(
            r#"{
                "input_device_id": null,
                "output_device_id": null,
                "asr_endpoint": "http://127.0.0.1:8000/v1",
                "asr_model": "local-asr",
                "tts_enabled": true,
                "tts_model": "cloud-tts",
                "tts_voice_id": ""
            }"#,
        )
        .expect("old metadata should remain readable");
        assert!(metadata.barge_in_enabled);
        assert!(metadata.tts_sapi_fallback);
    }

    #[test]
    fn tts_selection_prefers_cloud_and_falls_back_locally() {
        assert_eq!(
            select_tts_provider(true, true, true, true),
            TtsSelection::Aliyun
        );
        assert_eq!(
            select_tts_provider(true, false, false, true),
            TtsSelection::Sapi
        );
        assert_eq!(
            select_tts_provider(true, true, false, false),
            TtsSelection::MissingVoiceId
        );
        assert_eq!(
            select_tts_provider(false, false, false, false),
            TtsSelection::Disabled
        );
    }

    #[test]
    fn rejects_control_characters_in_device_ids() {
        assert!(validate_device_id(Some("device\nother")).is_err());
        assert!(validate_device_id(Some("wasapi:microphone")).is_ok());
    }
}
