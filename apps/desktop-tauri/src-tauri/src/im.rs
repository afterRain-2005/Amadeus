use std::{
    collections::VecDeque,
    fs, io,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};

use futures_util::{SinkExt, StreamExt};
use reqwest::Url;
use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Emitter};
use tauri_plugin_notification::NotificationExt;
use tokio_tungstenite::{
    connect_async_with_config,
    tungstenite::{
        Message,
        client::IntoClientRequest,
        http::{HeaderValue, header::AUTHORIZATION},
        protocol::WebSocketConfig,
    },
};
use tokio_util::sync::CancellationToken;
use zeroize::Zeroizing;

use crate::{
    config_io,
    settings::{read_secret, update_secret},
};

const CORE_EVENT: &str = "core-event";
const SETTINGS_FILE: &str = "im.json";
const DATABASE_FILE: &str = "im.db";
const CREDENTIAL_TARGET: &str = "com.wweiyi.amadeus.next/onebot-access-token";
const MAX_EVENT_BYTES: usize = 2 * 1024 * 1024;
const MAX_CONTENT_CHARS: usize = 2_000;
const MAX_SEEN: usize = 512;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ImSettings {
    pub enabled: bool,
    pub ws_url: String,
    pub group_at_only: bool,
    pub keywords: Vec<String>,
    pub bubble: bool,
    pub tray: bool,
    pub quiet_start: String,
    pub quiet_end: String,
}

impl Default for ImSettings {
    fn default() -> Self {
        Self {
            enabled: false,
            ws_url: "ws://127.0.0.1:3001".to_owned(),
            group_at_only: true,
            keywords: Vec::new(),
            bubble: true,
            tray: true,
            quiet_start: "23:00".to_owned(),
            quiet_end: "08:00".to_owned(),
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SaveImSettings {
    #[serde(flatten)]
    pub settings: ImSettings,
    /// `None` preserves the current token; an empty string removes it.
    pub access_token: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PublicImSettings {
    #[serde(flatten)]
    pub settings: ImSettings,
    pub has_access_token: bool,
    pub status: String,
    pub status_detail: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ImMessage {
    pub platform: String,
    pub message_type: String,
    pub peer_id: String,
    pub sender_name: String,
    pub content: String,
    pub is_at_me: bool,
    pub timestamp: i64,
    pub message_id: String,
}

impl ImMessage {
    fn display(&self) -> String {
        let kind = if self.message_type == "private" {
            "私聊".to_owned()
        } else {
            format!("群 {}", self.peer_id)
        };
        format!("【QQ·{kind}】{}：{}", self.sender_name, self.content)
    }
}

#[derive(Clone, Debug, Default)]
struct ImRuntime {
    status: String,
    detail: String,
    token: Option<CancellationToken>,
}

#[derive(Clone)]
pub struct ImState {
    settings_path: PathBuf,
    database_path: PathBuf,
    runtime: Arc<Mutex<ImRuntime>>,
    seen: Arc<Mutex<VecDeque<String>>>,
}

impl ImState {
    pub fn new(config_dir: PathBuf) -> Result<Self, String> {
        fs::create_dir_all(&config_dir)
            .map_err(|error| format!("创建 IM 配置目录失败：{error}"))?;
        let state = Self {
            settings_path: config_dir.join(SETTINGS_FILE),
            database_path: config_dir.join(DATABASE_FILE),
            runtime: Arc::new(Mutex::new(ImRuntime {
                status: "stopped".to_owned(),
                detail: "QQ 接入未启用".to_owned(),
                token: None,
            })),
            seen: Arc::new(Mutex::new(VecDeque::with_capacity(MAX_SEEN))),
        };
        state.initialize_database()?;
        state.import_legacy_settings();
        Ok(state)
    }

    pub fn public(&self) -> Result<PublicImSettings, String> {
        let settings = self.load_settings()?;
        let runtime = self
            .runtime
            .lock()
            .map_err(|_| "IM 状态锁已损坏".to_owned())?;
        Ok(PublicImSettings {
            settings,
            has_access_token: read_secret(CREDENTIAL_TARGET)?.is_some(),
            status: runtime.status.clone(),
            status_detail: runtime.detail.clone(),
        })
    }

    pub fn save(
        &self,
        mut input: SaveImSettings,
        app: AppHandle,
    ) -> Result<PublicImSettings, String> {
        validate_settings(&mut input.settings)?;
        update_secret(CREDENTIAL_TARGET, input.access_token.take())?;
        write_json(&self.settings_path, &input.settings)?;
        self.start(app)?;
        self.public()
    }

    pub fn start(&self, app: AppHandle) -> Result<(), String> {
        self.stop()?;
        let settings = self.load_settings()?;
        if !settings.enabled {
            self.set_status(&app, "stopped", "QQ 接入未启用");
            return Ok(());
        }
        let url = validate_ws_url(&settings.ws_url)?;
        let token = CancellationToken::new();
        self.runtime
            .lock()
            .map_err(|_| "IM 状态锁已损坏".to_owned())?
            .token = Some(token.clone());
        let state = self.clone();
        tauri::async_runtime::spawn(async move {
            state.run_connection(app, settings, url, token).await;
        });
        Ok(())
    }

    pub fn stop(&self) -> Result<(), String> {
        if let Some(token) = self
            .runtime
            .lock()
            .map_err(|_| "IM 状态锁已损坏".to_owned())?
            .token
            .take()
        {
            token.cancel();
        }
        Ok(())
    }

    pub fn recent(&self, limit: u32) -> Result<Vec<ImMessage>, String> {
        let connection = Connection::open(&self.database_path)
            .map_err(|error| format!("打开 IM 消息库失败：{error}"))?;
        let mut statement = connection
            .prepare(
                "SELECT platform, message_type, peer_id, sender_name, content, is_at_me, timestamp, message_id
                 FROM im_messages ORDER BY id DESC LIMIT ?1",
            )
            .map_err(|error| format!("读取 IM 消息失败：{error}"))?;
        let rows = statement
            .query_map(params![limit.clamp(1, 200)], |row| {
                Ok(ImMessage {
                    platform: row.get(0)?,
                    message_type: row.get(1)?,
                    peer_id: row.get(2)?,
                    sender_name: row.get(3)?,
                    content: row.get(4)?,
                    is_at_me: row.get::<_, i64>(5)? != 0,
                    timestamp: row.get(6)?,
                    message_id: row.get(7)?,
                })
            })
            .map_err(|error| format!("读取 IM 消息失败：{error}"))?;
        let mut messages = rows
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("解析 IM 消息失败：{error}"))?;
        messages.reverse();
        Ok(messages)
    }

    async fn run_connection(
        &self,
        app: AppHandle,
        settings: ImSettings,
        url: Url,
        cancellation: CancellationToken,
    ) {
        let mut backoff = 1u64;
        loop {
            if cancellation.is_cancelled() {
                self.set_status(&app, "stopped", "QQ 接入已停止");
                return;
            }
            self.set_status(&app, "connecting", url.as_str());
            let request = match build_request(&url, read_secret(CREDENTIAL_TARGET).ok().flatten()) {
                Ok(request) => request,
                Err(error) => {
                    self.set_status(&app, "error", &error);
                    return;
                }
            };
            let config = WebSocketConfig::default()
                .max_message_size(Some(MAX_EVENT_BYTES))
                .max_frame_size(Some(MAX_EVENT_BYTES));
            let connection = tokio::select! {
                _ = cancellation.cancelled() => return,
                result = tokio::time::timeout(
                    Duration::from_secs(8),
                    connect_async_with_config(request, Some(config), false),
                ) => result,
            };
            match connection {
                Ok(Ok((mut socket, _))) => {
                    self.set_status(&app, "connected", "OneBot 11 已连接");
                    backoff = 1;
                    loop {
                        let next = tokio::select! {
                            _ = cancellation.cancelled() => {
                                let _ = socket.close(None).await;
                                return;
                            }
                            message = socket.next() => message,
                        };
                        match next {
                            Some(Ok(Message::Text(text))) => {
                                self.handle_raw(&app, &settings, text.as_ref())
                            }
                            Some(Ok(Message::Binary(bytes))) if bytes.len() <= MAX_EVENT_BYTES => {
                                if let Ok(text) = std::str::from_utf8(bytes.as_ref()) {
                                    self.handle_raw(&app, &settings, text);
                                }
                            }
                            Some(Ok(Message::Ping(payload))) => {
                                if socket.send(Message::Pong(payload)).await.is_err() {
                                    break;
                                }
                            }
                            Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break,
                            _ => {}
                        }
                    }
                    self.set_status(&app, "disconnected", "连接中断，准备重连");
                }
                Ok(Err(error)) => {
                    self.set_status(&app, "disconnected", &bounded(&error.to_string(), 240))
                }
                Err(_) => self.set_status(&app, "disconnected", "连接超时，准备重连"),
            }
            tokio::select! {
                _ = cancellation.cancelled() => return,
                _ = tokio::time::sleep(Duration::from_secs(backoff)) => {}
            }
            backoff = (backoff * 2).min(60);
        }
    }

    fn handle_raw(&self, app: &AppHandle, settings: &ImSettings, raw: &str) {
        if raw.len() > MAX_EVENT_BYTES {
            return;
        }
        let Ok(event) = serde_json::from_str::<Value>(raw) else {
            return;
        };
        let Some(message) = parse_onebot_event(&event) else {
            return;
        };
        if self.is_duplicate(&message.message_id) {
            return;
        }
        let _ = self.store_message(&message);
        let _ = app.emit(
            CORE_EVENT,
            ImEvent::MessageReceived {
                message: message.clone(),
            },
        );
        if should_notify(settings, &message, current_minute_of_day()) {
            let display = message.display();
            if settings.bubble {
                let _ = app.emit(
                    CORE_EVENT,
                    ImEvent::Notification {
                        message: display.clone(),
                    },
                );
            }
            if settings.tray {
                let _ = app
                    .notification()
                    .builder()
                    .title("Amadeus · QQ")
                    .body(&display)
                    .show();
            }
        }
    }

    fn is_duplicate(&self, id: &str) -> bool {
        if id.is_empty() {
            return false;
        }
        let Ok(mut seen) = self.seen.lock() else {
            return false;
        };
        if seen.iter().any(|known| known == id) {
            return true;
        }
        if seen.len() == MAX_SEEN {
            seen.pop_front();
        }
        seen.push_back(id.to_owned());
        false
    }

    fn set_status(&self, app: &AppHandle, status: &str, detail: &str) {
        if let Ok(mut runtime) = self.runtime.lock() {
            runtime.status = status.to_owned();
            runtime.detail = bounded(detail, 240);
        }
        let _ = app.emit(
            CORE_EVENT,
            ImEvent::Status {
                status: status.to_owned(),
                detail: bounded(detail, 240),
            },
        );
    }

    fn initialize_database(&self) -> Result<(), String> {
        let connection = Connection::open(&self.database_path)
            .map_err(|error| format!("创建 IM 消息库失败：{error}"))?;
        connection
            .execute_batch(
                "PRAGMA journal_mode=WAL;
                 CREATE TABLE IF NOT EXISTS im_messages (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   platform TEXT NOT NULL,
                   message_type TEXT NOT NULL,
                   peer_id TEXT NOT NULL,
                   sender_name TEXT NOT NULL,
                   content TEXT NOT NULL,
                   is_at_me INTEGER NOT NULL,
                   timestamp INTEGER NOT NULL,
                   message_id TEXT NOT NULL
                 );
                 CREATE INDEX IF NOT EXISTS idx_im_timestamp ON im_messages(timestamp);",
            )
            .map_err(|error| format!("初始化 IM 消息库失败：{error}"))?;
        let cutoff = now_seconds() - 7 * 24 * 60 * 60;
        connection
            .execute(
                "DELETE FROM im_messages WHERE timestamp < ?1",
                params![cutoff],
            )
            .map_err(|error| format!("清理过期 IM 消息失败：{error}"))?;
        Ok(())
    }

    fn store_message(&self, message: &ImMessage) -> Result<(), String> {
        let connection = Connection::open(&self.database_path)
            .map_err(|error| format!("打开 IM 消息库失败：{error}"))?;
        connection
            .execute(
                "INSERT INTO im_messages
                 (platform, message_type, peer_id, sender_name, content, is_at_me, timestamp, message_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                params![
                    message.platform,
                    message.message_type,
                    message.peer_id,
                    message.sender_name,
                    message.content,
                    i64::from(message.is_at_me),
                    message.timestamp,
                    message.message_id,
                ],
            )
            .map_err(|error| format!("保存 IM 消息失败：{error}"))?;
        Ok(())
    }

    fn load_settings(&self) -> Result<ImSettings, String> {
        match fs::read(&self.settings_path) {
            Ok(bytes) => serde_json::from_slice(&bytes)
                .map_err(|error| format!("IM 设置文件已损坏：{error}")),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(ImSettings::default()),
            Err(error) => Err(format!("读取 IM 设置失败：{error}")),
        }
    }

    fn import_legacy_settings(&self) {
        if self.settings_path.exists() {
            return;
        }
        for path in legacy_config_candidates() {
            let Ok(bytes) = fs::read(path) else { continue };
            let Ok(root) = serde_json::from_slice::<Value>(&bytes) else {
                continue;
            };
            let Some(im) = root.get("im") else { continue };
            let mut settings = ImSettings::default();
            if let Some(qq) = im.get("qq") {
                settings.enabled = qq.get("enabled").and_then(Value::as_bool).unwrap_or(false);
                settings.ws_url = qq
                    .get("ws_url")
                    .and_then(Value::as_str)
                    .unwrap_or(&settings.ws_url)
                    .to_owned();
                settings.group_at_only = qq
                    .get("group_at_only")
                    .and_then(Value::as_bool)
                    .unwrap_or(true);
                settings.keywords = qq
                    .get("keywords")
                    .and_then(Value::as_array)
                    .map(|values| {
                        values
                            .iter()
                            .filter_map(Value::as_str)
                            .map(str::to_owned)
                            .collect()
                    })
                    .unwrap_or_default();
            }
            if let Some(notify) = im.get("notify") {
                settings.bubble = notify
                    .get("bubble")
                    .and_then(Value::as_bool)
                    .unwrap_or(true);
                settings.tray = notify.get("tray").and_then(Value::as_bool).unwrap_or(true);
            }
            if let Some(quiet) = im.get("quiet_hours") {
                settings.quiet_start = quiet
                    .get("start")
                    .and_then(Value::as_str)
                    .unwrap_or("23:00")
                    .to_owned();
                settings.quiet_end = quiet
                    .get("end")
                    .and_then(Value::as_str)
                    .unwrap_or("08:00")
                    .to_owned();
            }
            if validate_settings(&mut settings).is_ok() {
                let _ = write_json(&self.settings_path, &settings);
            }
            return;
        }
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "type")]
enum ImEvent {
    #[serde(rename = "imStatus")]
    Status { status: String, detail: String },
    #[serde(rename = "imMessageReceived")]
    MessageReceived { message: ImMessage },
    #[serde(rename = "imNotification")]
    Notification { message: String },
}

fn validate_settings(settings: &mut ImSettings) -> Result<(), String> {
    let url = validate_ws_url(&settings.ws_url)?;
    settings.ws_url = url.to_string();
    settings.keywords = settings
        .keywords
        .iter()
        .map(|value| bounded(value.trim(), 40))
        .filter(|value| !value.is_empty())
        .take(20)
        .collect();
    if parse_clock(&settings.quiet_start).is_none() || parse_clock(&settings.quiet_end).is_none() {
        return Err("IM 免打扰时段无效".to_owned());
    }
    Ok(())
}

fn validate_ws_url(raw: &str) -> Result<Url, String> {
    let url = Url::parse(raw.trim()).map_err(|_| "OneBot WebSocket URL 无效".to_owned())?;
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("OneBot URL 不得包含凭据、查询参数或片段；token 请单独保存".to_owned());
    }
    let loopback = match url.host_str() {
        Some("localhost") => true,
        Some(host) => host
            .parse::<std::net::IpAddr>()
            .is_ok_and(|ip| ip.is_loopback()),
        None => false,
    };
    if url.scheme() != "wss" && !(url.scheme() == "ws" && loopback) {
        return Err("远程 OneBot 连接必须使用 WSS；WS 仅允许本机地址".to_owned());
    }
    Ok(url)
}

fn build_request(
    url: &Url,
    token: Option<Zeroizing<String>>,
) -> Result<tokio_tungstenite::tungstenite::http::Request<()>, String> {
    let mut request = url
        .as_str()
        .into_client_request()
        .map_err(|error| format!("创建 OneBot 请求失败：{error}"))?;
    if let Some(token) = token {
        let value = HeaderValue::from_str(&format!("Bearer {}", token.as_str()))
            .map_err(|_| "OneBot access token 含有无效字符".to_owned())?;
        request.headers_mut().insert(AUTHORIZATION, value);
    }
    Ok(request)
}

fn parse_onebot_event(event: &Value) -> Option<ImMessage> {
    if event.get("post_type")?.as_str()? != "message" {
        return None;
    }
    let self_id = scalar_string(event.get("self_id"));
    let mut is_at_me = false;
    let content = match event.get("message").or_else(|| event.get("raw_message"))? {
        Value::String(text) => parse_cq_text(text, &self_id, &mut is_at_me),
        Value::Array(segments) => segments
            .iter()
            .filter_map(|segment| segment_text(segment, &self_id, &mut is_at_me))
            .collect::<String>(),
        _ => return None,
    };
    let sender = event.get("sender").unwrap_or(&Value::Null);
    let sender_name = ["card", "nickname", "user_id"]
        .iter()
        .find_map(|key| {
            sender
                .get(key)
                .map(|value| scalar_string(Some(value)))
                .filter(|value| !value.is_empty())
        })
        .unwrap_or_else(|| "未知".to_owned());
    let group = event.get("message_type").and_then(Value::as_str) == Some("group");
    let content = bounded(content.trim(), MAX_CONTENT_CHARS);
    if content.is_empty() {
        return None;
    }
    Some(ImMessage {
        platform: "qq".to_owned(),
        message_type: if group { "group" } else { "private" }.to_owned(),
        peer_id: scalar_string(event.get(if group { "group_id" } else { "user_id" })),
        sender_name: bounded(&sender_name, 80),
        content,
        is_at_me,
        timestamp: event
            .get("time")
            .and_then(Value::as_i64)
            .unwrap_or_else(now_seconds),
        message_id: scalar_string(event.get("message_id")),
    })
}

fn segment_text(segment: &Value, self_id: &str, is_at_me: &mut bool) -> Option<String> {
    let kind = segment.get("type")?.as_str()?;
    let data = segment.get("data").unwrap_or(&Value::Null);
    Some(match kind {
        "text" => data
            .get("text")
            .map(|value| scalar_string(Some(value)))
            .unwrap_or_default(),
        "at" => {
            let qq = scalar_string(data.get("qq"));
            *is_at_me |= !self_id.is_empty() && qq == self_id;
            if qq.is_empty() {
                "@某人".to_owned()
            } else {
                format!("@{qq}")
            }
        }
        "image" => "[图片]".to_owned(),
        "record" => "[语音]".to_owned(),
        "video" => "[视频]".to_owned(),
        "face" | "bface" => "[表情]".to_owned(),
        "mface" | "json" | "xml" => "[卡片]".to_owned(),
        "reply" => "[回复]".to_owned(),
        "file" => "[文件]".to_owned(),
        other => format!("[{other}]"),
    })
}

fn parse_cq_text(text: &str, self_id: &str, is_at_me: &mut bool) -> String {
    let mut output = String::new();
    let mut rest = text;
    while let Some(start) = rest.find("[CQ:") {
        output.push_str(&rest[..start]);
        let Some(end) = rest[start..].find(']') else {
            output.push_str(&rest[start..]);
            return output;
        };
        let code = &rest[start + 4..start + end];
        let mut parts = code.split(',');
        let kind = parts.next().unwrap_or_default();
        let qq = parts.find_map(|part| part.strip_prefix("qq="));
        let segment = serde_json::json!({"type": kind, "data": {"qq": qq.unwrap_or_default()}});
        output.push_str(&segment_text(&segment, self_id, is_at_me).unwrap_or_default());
        rest = &rest[start + end + 1..];
    }
    output.push_str(rest);
    output
}

fn should_notify(settings: &ImSettings, message: &ImMessage, minute: u32) -> bool {
    if in_quiet_hours(minute, &settings.quiet_start, &settings.quiet_end) {
        return false;
    }
    message.message_type != "group"
        || !settings.group_at_only
        || message.is_at_me
        || settings
            .keywords
            .iter()
            .any(|keyword| message.content.contains(keyword))
}

fn parse_clock(value: &str) -> Option<u32> {
    let (hour, minute) = value.split_once(':')?;
    let hour = hour.parse::<u32>().ok()?;
    let minute = minute.parse::<u32>().ok()?;
    (hour < 24 && minute < 60).then_some(hour * 60 + minute)
}

fn in_quiet_hours(minute: u32, start: &str, end: &str) -> bool {
    let start = parse_clock(start).unwrap_or(23 * 60);
    let end = parse_clock(end).unwrap_or(8 * 60);
    if start == end {
        false
    } else if start < end {
        (start..end).contains(&minute)
    } else {
        minute >= start || minute < end
    }
}

fn scalar_string(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(value)) => value.clone(),
        Some(Value::Number(value)) => value.to_string(),
        _ => String::new(),
    }
}

fn bounded(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}

fn now_seconds() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .unwrap_or_default()
}

fn write_json(path: &Path, value: &ImSettings) -> Result<(), String> {
    let bytes =
        serde_json::to_vec_pretty(value).map_err(|error| format!("序列化 IM 设置失败：{error}"))?;
    config_io::write_bytes(path, &bytes).map_err(|error| format!("写入 IM 设置失败：{error}"))
}

fn legacy_config_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(current) = std::env::current_dir() {
        candidates.push(current.join("data").join("config.json"));
    }
    if let Ok(executable) = std::env::current_exe()
        && let Some(directory) = executable.parent()
    {
        candidates.push(directory.join("data").join("config.json"));
        candidates.push(
            directory
                .join("..")
                .join("..")
                .join("data")
                .join("config.json"),
        );
    }
    candidates
}

#[cfg(windows)]
fn current_minute_of_day() -> u32 {
    use windows_sys::Win32::{Foundation::SYSTEMTIME, System::SystemInformation::GetLocalTime};
    let mut time = SYSTEMTIME::default();
    unsafe { GetLocalTime(&mut time) };
    u32::from(time.wHour) * 60 + u32::from(time.wMinute)
}

#[cfg(not(windows))]
fn current_minute_of_day() -> u32 {
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn private_event() -> Value {
        serde_json::json!({
            "post_type": "message", "message_type": "private", "self_id": 999,
            "user_id": 123, "message_id": 7, "time": 42,
            "sender": {"nickname": "真由理"}, "message": "嘟嘟噜[CQ:image,file=x]"
        })
    }

    #[test]
    fn parses_string_and_segment_events() {
        let message = parse_onebot_event(&private_event()).expect("message");
        assert_eq!(message.content, "嘟嘟噜[图片]");
        let group = serde_json::json!({
            "post_type":"message", "message_type":"group", "self_id":"999", "group_id":1,
            "sender":{"card":"助手"}, "message":[
                {"type":"at","data":{"qq":"999"}}, {"type":"text","data":{"text":" 紧急"}}
            ]
        });
        assert!(parse_onebot_event(&group).expect("group").is_at_me);
    }

    #[test]
    fn enforces_secure_remote_websockets() {
        assert!(validate_ws_url("ws://127.0.0.1:3001").is_ok());
        assert!(validate_ws_url("wss://example.com/onebot").is_ok());
        assert!(validate_ws_url("ws://example.com/onebot").is_err());
        assert!(validate_ws_url("ws://127.0.0.1:3001?access_token=secret").is_err());
    }

    #[test]
    fn filters_groups_and_midnight_quiet_hours() {
        let settings = ImSettings::default();
        let mut message = parse_onebot_event(&private_event()).expect("message");
        message.message_type = "group".to_owned();
        assert!(!should_notify(&settings, &message, 12 * 60));
        message.content = "这是紧急消息".to_owned();
        let mut settings = settings;
        settings.keywords.push("紧急".to_owned());
        assert!(should_notify(&settings, &message, 12 * 60));
        assert!(!should_notify(&settings, &message, 23 * 60 + 30));
    }

    #[test]
    fn malformed_and_oversized_events_are_rejected_or_bounded() {
        for event in [
            Value::Null,
            serde_json::json!({}),
            serde_json::json!({"post_type":"notice"}),
            serde_json::json!({"post_type":"message", "message": 42}),
            serde_json::json!({"post_type":"message", "message": [{"data":{}}]}),
        ] {
            assert!(parse_onebot_event(&event).is_none());
        }

        let event = serde_json::json!({
            "post_type":"message",
            "message_type":"private",
            "user_id":123,
            "sender":{"nickname":"sender"},
            "message":"x".repeat(MAX_CONTENT_CHARS * 10)
        });
        let message = parse_onebot_event(&event).expect("bounded long message");
        assert_eq!(message.content.chars().count(), MAX_CONTENT_CHARS);
    }
}
