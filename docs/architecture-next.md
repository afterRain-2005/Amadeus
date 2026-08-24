# Amadeus native architecture

Status: migration-complete Rust/Tauri architecture, application 0.10.0, protocol 4.

## Components

1. `apps/desktop-tauri`: borderless `280×560` Tauri/WebView2 phone shell plus independent settings
   and terminal windows; Live2D and WIRED Rose/Aqua presentation UI.
2. `crates/amadeus-core`: UI-independent protocol plus Windows Job Object process lifecycle.
3. Native data core: SQLite WAL conversations/memories/IM and atomic non-secret JSON settings.
4. Native audio core: CPAL capture/device discovery, adaptive VAD, ASR, Aliyun/SAPI TTS,
   Rodio playback, streaming sentence queue, NLMS AEC and barge-in recovery state machine.
5. Native integrations: memory-only GDI screen capture, OneBot WebSocket, companion sensors,
   notifications and bounded release checking.
6. Agent adapters: direct OpenAI-compatible streaming or a supervised local Codex CLI process.

The old Python tree remains only as migration provenance. It is not started, bundled or required
by the native application.

## Trust boundaries

- WebView code is presentation-only and crosses Rust through named Tauri commands/events.
- CSP disallows inline script/style and `unsafe-eval`; Live2D dependencies are vendored assets.
- No generic shell-string or `run_command` IPC is exposed.
- Model, ASR, TTS and OneBot credentials use separate Windows Credential Manager entries.
- Remote model/ASR endpoints require HTTPS; plain WS is OneBot-loopback-only; TTS media is
  HTTPS-only and restricted to expected Aliyun domains.
- Audio callbacks allocate no network/disk work and feed a preallocated SPSC ring.
- Screen capture is user-enabled per call, visible while active, downscaled in GDI, JPEG-bounded,
  attached to one request and never persisted.
- Clipboard access is separately opt-in and bounded; foreground context is marked untrusted in
  prompts so captured text cannot become instructions.
- Codex prompts use stdin rather than argv. The only sandboxes exposed are `read-only` and
  explicit `workspace-write`; child processes are attached to a kill-on-close Job Object.
- Every long-lived session owns cancellation state: chat, voice, TTS, IM, companion and Agent.

## Failure isolation

Direct text chat is the baseline path. Memory recall, companion sensing, QQ, TTS, version checks
and Codex availability may fail independently without preventing it. Voice recovery discards stale
capture, resets VAD/AEC state and retries with capped backoff. Configuration writes use same-folder
temporary files, flush, then replace to avoid exposing partial JSON after a crash.

## Evidence

- Rust unit/integration tests cover protocol, process teardown, settings validation, VAD/AEC,
  long-silence bounding, streaming speech, conversation concurrency/reopen, malformed IM events,
  proactive quota behavior and update parsing.
- Strict Clippy, TypeScript/Vite builds, npm audit and Windows-target RustSec audit pass.
- Debug and optimized embedded-asset executables render Live2D under strict CSP without a dev
  server; main-window drag, the independent settings/terminal windows and theme synchronization
  have been exercised on Windows.
- MSI and NSIS are built from the same 0.10.0 configuration. Publisher signing and clean-machine
  matrix testing remain release operations requiring the publisher identity/infrastructure.
