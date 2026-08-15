"""5 传感器 + ContextSnapshot。

每个传感器用 QTimer 周期轮询 win32 API，信号变化时更新内部状态。
build_snapshot 聚合所有传感器字段为 ContextSnapshot（喂给 LLM 决策器）。

隐私边界（产品化设计 §6）：
- Clipboard / Screen 默认关
- 不记录按键内容（只看空闲时长）
- 剪贴板含 password/key/token 等关键词时过滤
"""
from __future__ import annotations

import base64
import ctypes
import io
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

try:
    import mss
except ImportError:
    mss = None  # ScreenSensor 在 mss 缺失时降级为不可用

try:
    import win32clipboard
    import win32gui
    import win32process
except ImportError:
    win32clipboard = None
    win32gui = None
    win32process = None


SENSITIVE_PATTERN = re.compile(r"password|passwd|secret|api[_-]?key|token|credential", re.IGNORECASE)


# === win32 抽象层（便于测试 mock） ===

def _get_foreground_window() -> tuple[str, str, int]:
    """返回 (window_title, process_name, hwnd)。失败返回 ('', '', 0)。"""
    if win32gui is None or win32process is None:
        return ("", "", 0)
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        # 不在每次轮询调用 psutil 取进程名（成本高），返回 pid 由调用方按需解析
        return (title, str(pid), hwnd)
    except Exception:
        return ("", "", 0)


def _get_last_input_info() -> int:
    """返回最后输入的 tick count。失败返回 0。"""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
    return lii.dwTime


def _get_tick_count() -> int:
    return ctypes.windll.kernel32.GetTickCount()


def _get_clipboard_text() -> str:
    if win32clipboard is None:
        return ""
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData()
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return ""
    return ""


def _frame_to_b64(img) -> str:
    """mss 截帧对象转 base64 JPEG 字符串。"""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=60)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# === ContextSnapshot ===

@dataclass
class ContextSnapshot:
    timestamp: str
    local_time: str
    is_deep_night: bool
    idle_seconds: int
    work_session_minutes: int
    idle_state: str  # active / idle / away
    active_window_title: str
    active_process: str
    window_changed_recently: bool
    last_companion_greeting_ts: Optional[str]
    last_companion_topic: Optional[str]
    greeting_count_today: int
    clipboard_preview: Optional[str] = None
    screen_ocr_text: Optional[str] = None


# === 传感器 ===

class ActiveWindowSensor:
    def __init__(self, interval_seconds: int = 2) -> None:
        self.interval = interval_seconds
        self._window_title = ""
        self._process_name = ""
        self._since_ts: float = 0.0
        self._last_change_ts: float = 0.0
        self._timer = None  # QTimer 在 start() 时绑定

    def _poll(self) -> None:
        title, proc, _ = _get_foreground_window()
        if title != self._window_title:
            self._window_title = title
            self._process_name = proc
            self._since_ts = time.time()
            self._last_change_ts = time.time()

    def snapshot(self) -> dict:
        return {
            "window_title": self._window_title,
            "process_name": self._process_name,
            "since_ts": self._since_ts,
        }

    def window_changed_recently(self, window_seconds: int = 30) -> bool:
        return (time.time() - self._last_change_ts) < window_seconds

    def start(self, parent=None) -> None:
        from PySide6.QtCore import QTimer
        self._timer = QTimer(parent)
        self._timer.timeout.connect(self._poll)
        self._timer.start(self.interval * 1000)

    def stop(self) -> None:
        if self._timer:
            self._timer.stop()


class ActivityTracker:
    def __init__(self, interval_seconds: int = 30) -> None:
        self.interval = interval_seconds
        self.idle_seconds: int = 0
        self.work_session_minutes: int = 0
        self._last_active_ts: float = time.time()
        self._work_session_start_ts: float = time.time()
        self._timer = None

    def _poll(self) -> None:
        last_input = _get_last_input_info()
        tick = _get_tick_count()
        self.idle_seconds = max(0, (tick - last_input) // 1000)
        # 工作会话：连续输入（无 >5min 中断）累计
        if self.idle_seconds < 300:
            self.work_session_minutes = int((time.time() - self._work_session_start_ts) / 60)
        else:
            # 中断超过 5min，重置工作会话
            self._work_session_start_ts = time.time()
            self.work_session_minutes = 0
        self._last_active_ts = time.time()

    def start(self, parent=None) -> None:
        from PySide6.QtCore import QTimer
        self._timer = QTimer(parent)
        self._timer.timeout.connect(self._poll)
        self._timer.start(self.interval * 1000)

    def stop(self) -> None:
        if self._timer:
            self._timer.stop()


class IdleStateTracker:
    """派生自 ActivityTracker 数据，无需独立 QTimer。"""
    def __init__(self) -> None:
        self.idle_state: str = "active"
        self.since_ts: float = time.time()

    def update(self, idle_seconds: int) -> None:
        new_state = "active" if idle_seconds < 300 else ("idle" if idle_seconds < 900 else "away")
        if new_state != self.idle_state:
            self.idle_state = new_state
            self.since_ts = time.time()


class ClipboardSensor:
    def __init__(self, interval_seconds: int = 1, enabled: bool = False) -> None:
        self.interval = interval_seconds
        self.enabled = enabled
        self._current_text: str = ""
        self._hash: str = ""
        self._length: int = 0
        self._filtered: bool = False
        self._timer = None

    def _poll(self) -> None:
        if not self.enabled:
            return
        text = _get_clipboard_text()
        if not text or text == self._current_text:
            return
        self._current_text = text
        self._length = len(text)
        self._hash = str(hash(text))
        # 敏感内容过滤
        self._filtered = bool(SENSITIVE_PATTERN.search(text))

    def snapshot(self) -> dict:
        if not self.enabled:
            return {}
        if self._filtered or not self._current_text:
            return {"hash": self._hash, "length": self._length, "preview": None}
        return {
            "hash": self._hash,
            "length": self._length,
            "preview": self._current_text[:50],
        }

    def start(self, parent=None) -> None:
        if not self.enabled:
            return
        from PySide6.QtCore import QTimer
        self._timer = QTimer(parent)
        self._timer.timeout.connect(self._poll)
        self._timer.start(self.interval * 1000)

    def stop(self) -> None:
        if self._timer:
            self._timer.stop()


class ScreenSensor:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self._frame_b64: str = ""
        self._last_capture_ts: float = 0.0

    def capture(self) -> None:
        if not self.enabled or mss is None:
            return
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1] if sct.monitors else None
                if monitor:
                    img = sct.grab(monitor)
                    self._frame_b64 = _frame_to_b64(img)
                    self._last_capture_ts = time.time()
        except Exception:
            self._frame_b64 = ""

    def snapshot(self) -> dict:
        if not self.enabled:
            return {}
        return {"frame_jpg_b64": self._frame_b64, "captured_at": self._last_capture_ts}


# === 聚合 ===

def build_snapshot(
    *,
    active_window: ActiveWindowSensor,
    activity: ActivityTracker,
    idle: IdleStateTracker,
    clipboard: Optional[ClipboardSensor],
    screen: Optional[ScreenSensor],
    last_greeting_ts: Optional[str],
    last_topic: Optional[str],
    greeting_count: int,
    local_time: str,
    is_deep_night: bool,
) -> ContextSnapshot:
    aw_snap = active_window.snapshot()
    clip_snap = clipboard.snapshot() if clipboard else {}
    screen_snap = screen.snapshot() if screen else {}
    return ContextSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        local_time=local_time,
        is_deep_night=is_deep_night,
        idle_seconds=activity.idle_seconds,
        work_session_minutes=activity.work_session_minutes,
        idle_state=idle.idle_state,
        active_window_title=aw_snap.get("window_title", ""),
        active_process=aw_snap.get("process_name", ""),
        window_changed_recently=active_window.window_changed_recently(),
        last_companion_greeting_ts=last_greeting_ts,
        last_companion_topic=last_topic,
        greeting_count_today=greeting_count,
        clipboard_preview=clip_snap.get("preview"),
        screen_ocr_text=screen_snap.get("frame_jpg_b64"),  # 实际 OCR 在 LLM 侧处理
    )
