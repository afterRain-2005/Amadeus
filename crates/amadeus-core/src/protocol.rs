use serde::{Deserialize, Serialize};
use uuid::Uuid;

/// Increment only when a frontend/backend message becomes incompatible.
pub const PROTOCOL_VERSION: u16 = 4;

#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct SessionId(Uuid);

impl SessionId {
    #[must_use]
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }
}

impl Default for SessionId {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProtocolInfo {
    pub protocol_version: u16,
    pub app_version: String,
}

impl ProtocolInfo {
    #[must_use]
    pub fn current(app_version: impl Into<String>) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION,
            app_version: app_version.into(),
        }
    }
}

/// Commands accepted from the unprivileged WebView.
///
/// No variant contains a shell command. Privileged operations added later must
/// stay structured and pass an approval policy in the Rust core.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "camelCase")]
pub enum UiCommand {
    HideWindow,
    ShowWindow,
    StartChat {
        session_id: SessionId,
        text: String,
    },
    CancelSession {
        session_id: SessionId,
    },
    SetPointer {
        x: f32,
        y: f32,
    },
    SetExpression {
        emotion: String,
        motion: Option<String>,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum VoicePhase {
    Idle,
    Listening,
    Recording,
    Transcribing,
    Thinking,
    Speaking,
    Reconnecting,
    Ended,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "camelCase")]
pub enum CoreEvent {
    Ready {
        protocol_version: u16,
    },
    SessionStarted {
        session_id: SessionId,
    },
    ChatDelta {
        session_id: SessionId,
        text: String,
    },
    SessionFinished {
        session_id: SessionId,
    },
    SessionCancelled {
        session_id: SessionId,
    },
    VoicePhaseChanged {
        session_id: SessionId,
        phase: VoicePhase,
    },
    VoiceLevel {
        session_id: SessionId,
        /// Normalized microphone level in the inclusive range 0..=1000.
        level: u16,
    },
    VoiceTranscript {
        session_id: SessionId,
        text: String,
    },
    VoiceSubtitle {
        session_id: SessionId,
        text: String,
    },
    VoicePlaybackLevel {
        session_id: SessionId,
        /// Normalized output level in the inclusive range 0..=1000.
        level: u16,
    },
    VoiceScreenShareChanged {
        session_id: SessionId,
        enabled: bool,
    },
    AgentStatus {
        session_id: SessionId,
        text: String,
    },
    AgentToolEvent {
        session_id: SessionId,
        kind: String,
        title: String,
        detail: String,
        is_error: bool,
    },
    VoiceDeviceChanged {
        session_id: SessionId,
        input_name: String,
        sample_rate: u32,
    },
    VoiceDeviceRecovery {
        session_id: SessionId,
        attempt: u16,
        retry_in_ms: u64,
        message: String,
    },
    VoiceMutedChanged {
        session_id: SessionId,
        muted: bool,
    },
    Error {
        session_id: Option<SessionId>,
        code: String,
        message: String,
    },
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn protocol_round_trips_with_an_explicit_discriminator() {
        let command = UiCommand::StartChat {
            session_id: SessionId::new(),
            text: "こんにちは".to_owned(),
        };

        let json = serde_json::to_string(&command).expect("serialize command");
        assert!(json.contains("\"type\":\"startChat\""));
        assert_eq!(
            serde_json::from_str::<UiCommand>(&json).expect("deserialize command"),
            command
        );
    }

    #[test]
    fn protocol_info_uses_the_current_wire_version() {
        assert_eq!(
            ProtocolInfo::current("0.1.0").protocol_version,
            PROTOCOL_VERSION
        );
    }
}
