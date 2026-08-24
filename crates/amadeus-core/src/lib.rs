//! Stable, UI-independent core primitives for the next Amadeus desktop shell.
//!
//! The crate intentionally does not depend on Tauri. This keeps protocol,
//! process lifecycle, and later audio/agent state machines independently
//! testable.

pub mod process;
pub mod protocol;

pub use process::{
    ManagedProcessInfo, ManagedProcessPipes, ProcessError, ProcessSupervisor, SidecarSpec,
};
pub use protocol::{CoreEvent, PROTOCOL_VERSION, ProtocolInfo, SessionId, UiCommand, VoicePhase};
