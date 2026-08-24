use std::{
    fs, io,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};
use tokio_util::sync::CancellationToken;

use crate::config_io;

const CORE_EVENT: &str = "core-event";
const SETTINGS_FILE: &str = "companion.json";
const RUNTIME_FILE: &str = "companion-runtime.json";

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CompanionSettings {
    pub proactive_enabled: bool,
    pub active_window_enabled: bool,
    pub activity_enabled: bool,
    pub clipboard_enabled: bool,
    pub idle_minutes: u32,
    pub cooldown_minutes: u32,
    pub max_per_day: u32,
    pub quiet_start: String,
    pub quiet_end: String,
}

impl Default for CompanionSettings {
    fn default() -> Self {
        Self {
            proactive_enabled: true,
            active_window_enabled: true,
            activity_enabled: true,
            clipboard_enabled: false,
            idle_minutes: 20,
            cooldown_minutes: 45,
            max_per_day: 4,
            quiet_start: "23:00".to_owned(),
            quiet_end: "08:00".to_owned(),
        }
    }
}

#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PerceptionSnapshot {
    pub active_app: String,
    pub window_title: String,
    pub idle_seconds: u64,
    pub clipboard_preview: String,
    pub captured_at: i64,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CompanionPublicState {
    #[serde(flatten)]
    pub settings: CompanionSettings,
    pub snapshot: PerceptionSnapshot,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
struct CompanionRuntime {
    last_greeting_ms: i64,
    day_key: u32,
    count_today: u32,
}

#[derive(Clone)]
pub struct PerceptionState {
    settings_path: PathBuf,
    runtime_path: PathBuf,
    snapshot: Arc<Mutex<PerceptionSnapshot>>,
    cancellation: Arc<Mutex<Option<CancellationToken>>>,
}

impl PerceptionState {
    pub fn new(config_dir: PathBuf) -> Result<Self, String> {
        fs::create_dir_all(&config_dir)
            .map_err(|error| format!("创建 Companion 配置目录失败：{error}"))?;
        Ok(Self {
            settings_path: config_dir.join(SETTINGS_FILE),
            runtime_path: config_dir.join(RUNTIME_FILE),
            snapshot: Arc::new(Mutex::new(PerceptionSnapshot::default())),
            cancellation: Arc::new(Mutex::new(None)),
        })
    }

    pub fn public(&self) -> Result<CompanionPublicState, String> {
        Ok(CompanionPublicState {
            settings: self.load_settings()?,
            snapshot: self.snapshot()?,
        })
    }

    pub fn save(&self, settings: CompanionSettings) -> Result<CompanionPublicState, String> {
        validate_settings(&settings)?;
        write_json(&self.settings_path, &settings, "Companion 设置")?;
        self.refresh()?;
        self.public()
    }

    pub fn snapshot(&self) -> Result<PerceptionSnapshot, String> {
        self.snapshot
            .lock()
            .map(|snapshot| snapshot.clone())
            .map_err(|_| "感知状态锁已损坏".to_owned())
    }

    pub fn context_for_prompt(&self) -> Result<String, String> {
        let settings = self.load_settings()?;
        let snapshot = self.snapshot()?;
        let mut context = Vec::new();
        if settings.active_window_enabled && !snapshot.active_app.is_empty() {
            context.push(format!(
                "当前应用={}，窗口标题={}",
                snapshot.active_app, snapshot.window_title
            ));
        }
        if settings.activity_enabled {
            context.push(format!("用户空闲约 {} 秒", snapshot.idle_seconds));
        }
        if settings.clipboard_enabled && !snapshot.clipboard_preview.is_empty() {
            context.push(format!("剪贴板预览={}", snapshot.clipboard_preview));
        }
        Ok(context.join("；"))
    }

    pub fn refresh(&self) -> Result<PerceptionSnapshot, String> {
        let settings = self.load_settings()?;
        let captured = capture_snapshot(&settings);
        *self
            .snapshot
            .lock()
            .map_err(|_| "感知状态锁已损坏".to_owned())? = captured.clone();
        Ok(captured)
    }

    pub fn start(&self, app: AppHandle) -> Result<(), String> {
        let mut running = self
            .cancellation
            .lock()
            .map_err(|_| "感知服务状态锁已损坏".to_owned())?;
        if running
            .as_ref()
            .is_some_and(|cancellation| !cancellation.is_cancelled())
        {
            return Ok(());
        }
        let cancellation = CancellationToken::new();
        *running = Some(cancellation.clone());
        drop(running);
        let state = self.clone();
        tauri::async_runtime::spawn(async move {
            let mut previous_idle = 0;
            loop {
                if let Ok(snapshot) = state.refresh() {
                    let _ = app.emit(
                        CORE_EVENT,
                        PerceptionEvent::PerceptionUpdated {
                            snapshot: snapshot.clone(),
                        },
                    );
                    if let Ok(settings) = state.load_settings()
                        && settings.proactive_enabled
                        && let Some(message) =
                            state.proactive_message(&settings, &snapshot, previous_idle, false)
                    {
                        let _ = app.emit(CORE_EVENT, PerceptionEvent::ProactiveMessage { message });
                    }
                    previous_idle = snapshot.idle_seconds;
                }
                tokio::select! {
                    _ = cancellation.cancelled() => return,
                    _ = tokio::time::sleep(Duration::from_secs(2)) => {}
                }
            }
        });
        Ok(())
    }

    pub fn stop(&self) -> Result<bool, String> {
        let cancellation = self
            .cancellation
            .lock()
            .map_err(|_| "感知服务状态锁已损坏".to_owned())?
            .take();
        if let Some(cancellation) = cancellation {
            cancellation.cancel();
            Ok(true)
        } else {
            Ok(false)
        }
    }

    pub fn test_greeting(&self, app: &AppHandle) -> Result<String, String> {
        let settings = self.load_settings()?;
        let snapshot = self.refresh()?;
        let message = self
            .proactive_message(&settings, &snapshot, 0, true)
            .unwrap_or_else(|| "测试信号收到。别误会，我只是在确认线路正常。".to_owned());
        let _ = app.emit(
            CORE_EVENT,
            PerceptionEvent::ProactiveMessage {
                message: message.clone(),
            },
        );
        Ok(message)
    }

    fn proactive_message(
        &self,
        settings: &CompanionSettings,
        snapshot: &PerceptionSnapshot,
        previous_idle: u64,
        force: bool,
    ) -> Option<String> {
        let local = local_time();
        let mut runtime = self.load_runtime();
        if runtime.day_key != local.day_key {
            runtime.day_key = local.day_key;
            runtime.count_today = 0;
        }
        if !force
            && (in_quiet_hours(
                local.minute_of_day,
                &settings.quiet_start,
                &settings.quiet_end,
            ) || runtime.count_today >= settings.max_per_day
                || now_ms().saturating_sub(runtime.last_greeting_ms)
                    < i64::from(settings.cooldown_minutes) * 60_000)
        {
            return None;
        }
        let idle_threshold = u64::from(settings.idle_minutes) * 60;
        let message = if force {
            "测试信号收到。主动陪伴调度器和感知层都在线。".to_owned()
        } else if previous_idle >= idle_threshold && snapshot.idle_seconds < 30 {
            "终于回来了。休息完就喝口水，再继续也不迟。".to_owned()
        } else if previous_idle < idle_threshold && snapshot.idle_seconds >= idle_threshold {
            "离开这么久……去休息也好，别回来又一口气坐到深夜。".to_owned()
        } else if (21 * 60..23 * 60).contains(&local.minute_of_day) && snapshot.idle_seconds < 60 {
            "已经很晚了。你可以再做一会儿，但别拿明天的精神状态做实验。".to_owned()
        } else {
            return None;
        };
        if !force {
            runtime.last_greeting_ms = now_ms();
            runtime.count_today = runtime.count_today.saturating_add(1);
            let _ = write_json(&self.runtime_path, &runtime, "Companion 运行状态");
        }
        Some(message)
    }

    fn load_settings(&self) -> Result<CompanionSettings, String> {
        read_json_or_default(&self.settings_path, "Companion 设置")
    }

    fn load_runtime(&self) -> CompanionRuntime {
        read_json_or_default(&self.runtime_path, "Companion 运行状态").unwrap_or_default()
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "type", rename_all = "camelCase")]
enum PerceptionEvent {
    PerceptionUpdated { snapshot: PerceptionSnapshot },
    ProactiveMessage { message: String },
}

fn validate_settings(settings: &CompanionSettings) -> Result<(), String> {
    if !(1..=240).contains(&settings.idle_minutes)
        || !(5..=1440).contains(&settings.cooldown_minutes)
        || settings.max_per_day > 24
        || parse_clock(&settings.quiet_start).is_none()
        || parse_clock(&settings.quiet_end).is_none()
    {
        return Err("Companion 时间或次数设置无效".to_owned());
    }
    Ok(())
}

fn in_quiet_hours(now: u32, start: &str, end: &str) -> bool {
    let (Some(start), Some(end)) = (parse_clock(start), parse_clock(end)) else {
        return false;
    };
    if start == end {
        false
    } else if start < end {
        (start..end).contains(&now)
    } else {
        now >= start || now < end
    }
}

fn parse_clock(value: &str) -> Option<u32> {
    let (hour, minute) = value.split_once(':')?;
    let hour = hour.parse::<u32>().ok()?;
    let minute = minute.parse::<u32>().ok()?;
    (hour < 24 && minute < 60).then_some(hour * 60 + minute)
}

fn read_json_or_default<T>(path: &Path, label: &str) -> Result<T, String>
where
    T: serde::de::DeserializeOwned + Default,
{
    match fs::read(path) {
        Ok(bytes) => {
            serde_json::from_slice(&bytes).map_err(|error| format!("{label}文件已损坏：{error}"))
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(T::default()),
        Err(error) => Err(format!("读取{label}失败：{error}")),
    }
}

fn write_json<T: Serialize>(path: &Path, value: &T, label: &str) -> Result<(), String> {
    let bytes =
        serde_json::to_vec_pretty(value).map_err(|error| format!("序列化{label}失败：{error}"))?;
    config_io::write_bytes(path, &bytes).map_err(|error| format!("写入{label}失败：{error}"))
}

fn capture_snapshot(settings: &CompanionSettings) -> PerceptionSnapshot {
    let (active_app, window_title) = if settings.active_window_enabled {
        platform::active_window().unwrap_or_default()
    } else {
        (String::new(), String::new())
    };
    PerceptionSnapshot {
        active_app,
        window_title,
        idle_seconds: if settings.activity_enabled {
            platform::idle_seconds().unwrap_or_default()
        } else {
            0
        },
        clipboard_preview: if settings.clipboard_enabled {
            platform::clipboard_text().unwrap_or_default()
        } else {
            String::new()
        },
        captured_at: now_ms(),
    }
}

struct LocalTime {
    minute_of_day: u32,
    day_key: u32,
}

fn local_time() -> LocalTime {
    platform::local_time().unwrap_or(LocalTime {
        minute_of_day: 12 * 60,
        day_key: 0,
    })
}

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}

fn bounded_text(value: String, limit: usize) -> String {
    value
        .chars()
        .filter(|character| !character.is_control() || *character == '\n')
        .take(limit)
        .collect::<String>()
        .trim()
        .to_owned()
}

#[cfg(windows)]
mod platform {
    use std::{ffi::OsString, os::windows::ffi::OsStringExt, path::PathBuf, ptr};

    use windows_sys::Win32::{
        Foundation::{CloseHandle, SYSTEMTIME},
        System::{
            DataExchange::{CloseClipboard, GetClipboardData, OpenClipboard},
            Memory::{GlobalLock, GlobalUnlock},
            SystemInformation::{GetLocalTime, GetTickCount64},
            Threading::{
                OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION, QueryFullProcessImageNameW,
            },
        },
        UI::{
            Input::KeyboardAndMouse::{GetLastInputInfo, LASTINPUTINFO},
            WindowsAndMessaging::{GetForegroundWindow, GetWindowTextW, GetWindowThreadProcessId},
        },
    };

    use super::{LocalTime, bounded_text};

    pub fn active_window() -> Option<(String, String)> {
        let window = unsafe { GetForegroundWindow() };
        if window.is_null() {
            return None;
        }
        let mut title = [0u16; 512];
        let length = unsafe { GetWindowTextW(window, title.as_mut_ptr(), title.len() as i32) };
        let title = bounded_text(
            OsString::from_wide(&title[..length.max(0) as usize])
                .to_string_lossy()
                .into_owned(),
            180,
        );
        let mut pid = 0;
        unsafe { GetWindowThreadProcessId(window, &mut pid) };
        let process = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
        let app = if process.is_null() {
            String::new()
        } else {
            let mut path = [0u16; 1024];
            let mut size = path.len() as u32;
            let ok =
                unsafe { QueryFullProcessImageNameW(process, 0, path.as_mut_ptr(), &mut size) };
            unsafe { CloseHandle(process) };
            if ok == 0 {
                String::new()
            } else {
                PathBuf::from(OsString::from_wide(&path[..size as usize]))
                    .file_name()
                    .map(|name| name.to_string_lossy().into_owned())
                    .unwrap_or_default()
            }
        };
        Some((bounded_text(app, 80), title))
    }

    pub fn idle_seconds() -> Option<u64> {
        let mut info = LASTINPUTINFO {
            cbSize: std::mem::size_of::<LASTINPUTINFO>() as u32,
            dwTime: 0,
        };
        if unsafe { GetLastInputInfo(&mut info) } == 0 {
            return None;
        }
        let now = unsafe { GetTickCount64() } as u32;
        Some(u64::from(now.wrapping_sub(info.dwTime)) / 1000)
    }

    pub fn clipboard_text() -> Option<String> {
        if unsafe { OpenClipboard(ptr::null_mut()) } == 0 {
            return None;
        }
        const CF_UNICODETEXT: u32 = 13;
        let handle = unsafe { GetClipboardData(CF_UNICODETEXT) };
        if handle.is_null() {
            unsafe { CloseClipboard() };
            return None;
        }
        let pointer = unsafe { GlobalLock(handle) }.cast::<u16>();
        if pointer.is_null() {
            unsafe { CloseClipboard() };
            return None;
        }
        let mut length = 0usize;
        while length < 4096 && unsafe { *pointer.add(length) } != 0 {
            length += 1;
        }
        let value = bounded_text(
            OsString::from_wide(unsafe { std::slice::from_raw_parts(pointer, length) })
                .to_string_lossy()
                .into_owned(),
            300,
        );
        unsafe {
            GlobalUnlock(handle);
            CloseClipboard();
        }
        Some(value)
    }

    pub fn local_time() -> Option<LocalTime> {
        let mut time = SYSTEMTIME::default();
        unsafe { GetLocalTime(&mut time) };
        Some(LocalTime {
            minute_of_day: u32::from(time.wHour) * 60 + u32::from(time.wMinute),
            day_key: u32::from(time.wYear) * 10_000
                + u32::from(time.wMonth) * 100
                + u32::from(time.wDay),
        })
    }
}

#[cfg(not(windows))]
mod platform {
    use super::LocalTime;

    pub fn active_window() -> Option<(String, String)> {
        None
    }
    pub fn idle_seconds() -> Option<u64> {
        None
    }
    pub fn clipboard_text() -> Option<String> {
        None
    }
    pub fn local_time() -> Option<LocalTime> {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quiet_hours_support_midnight_wraparound() {
        assert!(in_quiet_hours(23 * 60 + 30, "23:00", "08:00"));
        assert!(in_quiet_hours(7 * 60, "23:00", "08:00"));
        assert!(!in_quiet_hours(12 * 60, "23:00", "08:00"));
        assert!(!in_quiet_hours(12 * 60, "12:00", "12:00"));
    }

    #[test]
    fn companion_defaults_keep_sensitive_clipboard_off() {
        let settings = CompanionSettings::default();
        assert!(settings.active_window_enabled);
        assert!(settings.activity_enabled);
        assert!(!settings.clipboard_enabled);
        assert!(validate_settings(&settings).is_ok());
    }

    #[test]
    fn test_greeting_does_not_consume_proactive_quota() {
        let directory =
            std::env::temp_dir().join(format!("amadeus-perception-test-{}", uuid::Uuid::new_v4()));
        let state = PerceptionState::new(directory.clone()).expect("create perception state");
        let settings = CompanionSettings::default();
        let snapshot = PerceptionSnapshot::default();

        assert!(
            state
                .proactive_message(&settings, &snapshot, 0, true)
                .is_some()
        );
        assert!(!state.runtime_path.exists());

        let _ = fs::remove_dir_all(directory);
    }
}
