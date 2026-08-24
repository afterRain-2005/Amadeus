use std::{
    collections::HashMap,
    env, fs, io,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};

use crate::config_io;

const SETTINGS_FILE: &str = "agent.json";

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, Eq, PartialEq)]
#[serde(rename_all = "camelCase")]
pub enum AgentMode {
    #[default]
    Direct,
    Codex,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, Eq, PartialEq)]
#[serde(rename_all = "kebab-case")]
pub enum CodexSandbox {
    #[default]
    ReadOnly,
    WorkspaceWrite,
}

impl CodexSandbox {
    pub fn as_cli_value(self) -> &'static str {
        match self {
            Self::ReadOnly => "read-only",
            Self::WorkspaceWrite => "workspace-write",
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SaveAgentSettings {
    pub mode: AgentMode,
    pub workspace: String,
    pub sandbox: CodexSandbox,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PublicAgentSettings {
    pub mode: AgentMode,
    pub workspace: String,
    pub sandbox: CodexSandbox,
    pub codex_available: bool,
    pub codex_version: Option<String>,
}

#[derive(Clone, Debug)]
pub struct CodexConfiguration {
    pub executable: PathBuf,
    pub workspace: PathBuf,
    pub sandbox: CodexSandbox,
    pub thread_id: Option<String>,
    pub output_path: PathBuf,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct AgentMetadata {
    #[serde(default)]
    mode: AgentMode,
    workspace: String,
    #[serde(default)]
    sandbox: CodexSandbox,
    #[serde(default)]
    codex_threads: HashMap<String, String>,
}

#[derive(Clone)]
pub struct AgentSettingsStore {
    path: PathBuf,
    default_workspace: PathBuf,
    runtime_dir: PathBuf,
}

impl AgentSettingsStore {
    pub fn new(config_dir: PathBuf) -> Result<Self, String> {
        let default_workspace = config_dir.join("codex-workspace");
        let runtime_dir = config_dir.join("runtime");
        fs::create_dir_all(&default_workspace)
            .map_err(|error| format!("创建 Codex 默认工作区失败：{error}"))?;
        fs::create_dir_all(&runtime_dir)
            .map_err(|error| format!("创建 Agent 运行目录失败：{error}"))?;
        Ok(Self {
            path: config_dir.join(SETTINGS_FILE),
            default_workspace,
            runtime_dir,
        })
    }

    pub fn public(&self) -> Result<PublicAgentSettings, String> {
        let metadata = self.load_metadata()?;
        let probe = probe_codex();
        Ok(PublicAgentSettings {
            mode: metadata.mode,
            workspace: metadata.workspace,
            sandbox: metadata.sandbox,
            codex_available: probe.is_some(),
            codex_version: probe.map(|(_, version)| version),
        })
    }

    pub fn save(&self, input: SaveAgentSettings) -> Result<PublicAgentSettings, String> {
        let workspace = validate_workspace(&input.workspace)?;
        let mut metadata = self.load_metadata()?;
        metadata.mode = input.mode;
        metadata.workspace = workspace.to_string_lossy().into_owned();
        metadata.sandbox = input.sandbox;
        self.write_metadata(&metadata)?;
        self.public()
    }

    pub fn mode(&self) -> Result<AgentMode, String> {
        self.load_metadata().map(|metadata| metadata.mode)
    }

    pub fn codex_configuration(
        &self,
        conversation_id: &str,
        process_id: &str,
    ) -> Result<CodexConfiguration, String> {
        let metadata = self.load_metadata()?;
        let (executable, _) = probe_codex()
            .ok_or_else(|| "未找到 Codex CLI；请先安装并登录 Codex，或切回直连模式".to_owned())?;
        let workspace = validate_workspace(&metadata.workspace)?;
        Ok(CodexConfiguration {
            executable,
            workspace,
            sandbox: metadata.sandbox,
            thread_id: metadata.codex_threads.get(conversation_id).cloned(),
            output_path: self.runtime_dir.join(format!("{process_id}-last.txt")),
        })
    }

    pub fn remember_thread(&self, conversation_id: &str, thread_id: &str) -> Result<(), String> {
        if !valid_identifier(conversation_id) || !valid_identifier(thread_id) {
            return Err("Codex 会话标识无效".to_owned());
        }
        let mut metadata = self.load_metadata()?;
        metadata
            .codex_threads
            .insert(conversation_id.to_owned(), thread_id.to_owned());
        self.write_metadata(&metadata)
    }

    fn load_metadata(&self) -> Result<AgentMetadata, String> {
        match fs::read(&self.path) {
            Ok(bytes) => serde_json::from_slice(&bytes)
                .map_err(|error| format!("Agent 设置文件已损坏：{error}")),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(AgentMetadata {
                mode: AgentMode::Direct,
                workspace: self.default_workspace.to_string_lossy().into_owned(),
                sandbox: CodexSandbox::ReadOnly,
                codex_threads: HashMap::new(),
            }),
            Err(error) => Err(format!("读取 Agent 设置失败：{error}")),
        }
    }

    fn write_metadata(&self, metadata: &AgentMetadata) -> Result<(), String> {
        let bytes = serde_json::to_vec_pretty(metadata)
            .map_err(|error| format!("序列化 Agent 设置失败：{error}"))?;
        config_io::write_bytes(&self.path, &bytes)
            .map_err(|error| format!("写入 Agent 设置失败：{error}"))
    }
}

fn validate_workspace(value: &str) -> Result<PathBuf, String> {
    let path = PathBuf::from(value.trim());
    if !path.is_absolute() {
        return Err("Codex 工作区必须是绝对路径".to_owned());
    }
    let path = path
        .canonicalize()
        .map_err(|error| format!("Codex 工作区不存在或不可访问：{error}"))?;
    if !path.is_dir() {
        return Err("Codex 工作区必须是现有目录".to_owned());
    }
    Ok(path)
}

fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.chars().all(|character| {
            character.is_ascii_alphanumeric() || character == '-' || character == '_'
        })
}

pub fn probe_codex() -> Option<(PathBuf, String)> {
    for candidate in codex_candidates() {
        if !candidate.is_file() {
            continue;
        }
        let Some(output) = probe_candidate(&candidate, Duration::from_secs(2)) else {
            continue;
        };
        if output.status.success() {
            let version = String::from_utf8_lossy(&output.stdout).trim().to_owned();
            if version.starts_with("codex-cli ") {
                return Some((candidate, version));
            }
        }
    }
    None
}

fn probe_candidate(path: &Path, timeout: Duration) -> Option<std::process::Output> {
    let mut child = Command::new(path)
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return child.wait_with_output().ok(),
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(25)),
            Ok(None) | Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
}

fn codex_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(paths) = env::var_os("PATH") {
        for path in env::split_paths(&paths) {
            candidates.push(path.join("codex.exe"));
        }
    }
    if let Some(app_data) = env::var_os("APPDATA") {
        let packages = PathBuf::from(app_data)
            .join("npm")
            .join("node_modules")
            .join("@openai")
            .join("codex")
            .join("node_modules")
            .join("@openai");
        if let Ok(entries) = fs::read_dir(packages) {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().into_owned();
                if !name.starts_with("codex-win32-") {
                    continue;
                }
                let vendor = entry.path().join("vendor");
                if let Ok(targets) = fs::read_dir(vendor) {
                    for target in targets.flatten() {
                        candidates.push(target.path().join("bin").join("codex.exe"));
                    }
                }
            }
        }
    }
    let mut unique = std::collections::HashSet::new();
    candidates
        .into_iter()
        .filter(|path| unique.insert(path.clone()))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn workspace_validation_rejects_relative_and_missing_paths() {
        assert!(validate_workspace("relative").is_err());
        assert!(validate_workspace("Z:\\definitely-missing-amadeus-workspace").is_err());
    }

    #[test]
    fn never_exposes_danger_full_access_as_a_setting() {
        assert_eq!(CodexSandbox::ReadOnly.as_cli_value(), "read-only");
        assert_eq!(
            CodexSandbox::WorkspaceWrite.as_cli_value(),
            "workspace-write"
        );
    }
}
