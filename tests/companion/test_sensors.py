"""5 传感器 + ContextSnapshot 测试。所有 win32/mss 调用均 mock。

覆盖：
- ContextSnapshot dataclass 默认值与全字段
- ActiveWindowSensor: 快照/变化检测/无变化/边界
- ActivityTracker: 空闲秒/工作会话累计/重置
- IdleStateTracker: 三态转移 + since_ts 更新
- ClipboardSensor: 变化/过滤/禁用/空文本/hash
- ScreenSensor: 禁用/启用/mss 缺失/异常容灾
- build_snapshot: 全传感器聚合/全禁用场景
- win32 抽象层: 前台窗口/最后输入/tick/剪贴板
"""
from unittest.mock import patch, MagicMock

from core.companion.sensors import (
    ContextSnapshot, ActiveWindowSensor, ActivityTracker,
    IdleStateTracker, ClipboardSensor, ScreenSensor, build_snapshot,
    _get_foreground_window, _get_last_input_info, _get_tick_count,
    _get_clipboard_text, _frame_to_b64,
)


def test_context_snapshot_dataclass_defaults():
    snap = ContextSnapshot(
        timestamp="2026-08-16T10:00:00+00:00",
        local_time="14:30 周二", is_deep_night=False,
        idle_seconds=10, work_session_minutes=5, idle_state="active",
        active_window_title="main.py - Code", active_process="Code.exe",
        window_changed_recently=False,
        last_companion_greeting_ts=None, last_companion_topic=None,
        greeting_count_today=0,
    )
    assert snap.clipboard_preview is None
    assert snap.screen_ocr_text is None


def test_active_window_sensor_snapshot():
    sensor = ActiveWindowSensor(interval_seconds=2)
    with patch("core.companion.sensors._get_foreground_window",
               return_value=("main.py - Code", "Code.exe", 12345)):
        sensor._poll()
    snap = sensor.snapshot()
    assert snap["window_title"] == "main.py - Code"
    assert snap["process_name"] == "Code.exe"
    assert "since_ts" in snap


def test_active_window_sensor_detects_change():
    sensor = ActiveWindowSensor(interval_seconds=2)
    with patch("core.companion.sensors._get_foreground_window",
               return_value=("A", "A.exe", 1)):
        sensor._poll()
    with patch("core.companion.sensors._get_foreground_window",
               return_value=("B", "B.exe", 2)):
        sensor._poll()
    assert sensor.window_changed_recently() is True


def test_activity_tracker_idle_seconds():
    import ctypes
    sensor = ActivityTracker(interval_seconds=30)
    fake_info = MagicMock()
    fake_info.dwTime = 1000  # last input tick
    with patch("core.companion.sensors._get_last_input_info", return_value=1000), \
         patch("core.companion.sensors._get_tick_count", return_value=7000):
        sensor._poll()
    assert sensor.idle_seconds == 6


def test_idle_state_tracker_states():
    """active (<5min) / idle (5-15min) / away (>15min)"""
    tracker = IdleStateTracker()
    tracker.update(idle_seconds=10)
    assert tracker.idle_state == "active"
    tracker.update(idle_seconds=600)
    assert tracker.idle_state == "idle"
    tracker.update(idle_seconds=1200)
    assert tracker.idle_state == "away"


def test_clipboard_sensor_detects_change():
    sensor = ClipboardSensor(interval_seconds=1, enabled=True)
    with patch("core.companion.sensors._get_clipboard_text", return_value="hello"):
        sensor._poll()
    with patch("core.companion.sensors._get_clipboard_text", return_value="world"):
        sensor._poll()
    snap = sensor.snapshot()
    assert snap["preview"] == "world"
    assert snap["length"] == 5


def test_clipboard_sensor_filters_sensitive_content():
    """含 password/key/token 关键词的剪贴板内容不发送给 LLM。"""
    sensor = ClipboardSensor(interval_seconds=1, enabled=True)
    with patch("core.companion.sensors._get_clipboard_text",
               return_value="my_password=abc123"):
        sensor._poll()
    snap = sensor.snapshot()
    assert snap["preview"] is None  # 被过滤


def test_screen_sensor_disabled_by_default():
    sensor = ScreenSensor()
    assert sensor.snapshot() == {}


def test_screen_sensor_captures_when_enabled():
    sensor = ScreenSensor(enabled=True)
    fake_img = MagicMock()
    fake_img.tobytes.return_value = b"\x89PNG fake jpg data"
    with patch("core.companion.sensors.mss.mss") as mock_mss:
        mock_mss.return_value.__enter__.return_value.grab.return_value = fake_img
        with patch("core.companion.sensors._frame_to_b64", return_value="BASE64STR"):
            sensor.capture()
    snap = sensor.snapshot()
    assert snap.get("frame_jpg_b64") == "BASE64STR"


def test_build_snapshot_aggregates_all_sensors():
    """build_snapshot 把所有传感器字段聚合到 ContextSnapshot。"""
    aw = ActiveWindowSensor(interval_seconds=2)
    with patch("core.companion.sensors._get_foreground_window",
               return_value=("main.py - Code", "Code.exe", 1)):
        aw._poll()
    at = ActivityTracker(interval_seconds=30)
    with patch("core.companion.sensors._get_last_input_info", return_value=0), \
         patch("core.companion.sensors._get_tick_count", return_value=0):
        at._poll()
    it = IdleStateTracker()
    it.update(idle_seconds=at.idle_seconds)

    snap = build_snapshot(
        active_window=aw, activity=at, idle=it,
        clipboard=None, screen=None,
        last_greeting_ts=None, last_topic=None, greeting_count=0,
        local_time="14:30 周二", is_deep_night=False,
    )
    assert snap.active_window_title == "main.py - Code"
    assert snap.active_process == "Code.exe"
    assert snap.idle_state == "active"
    assert snap.clipboard_preview is None


# === ActiveWindowSensor 边界 ===

def test_active_window_sensor_no_change_does_not_update():
    """相同窗口标题不应更新 _last_change_ts。"""
    sensor = ActiveWindowSensor(interval_seconds=2)
    with patch("core.companion.sensors._get_foreground_window",
               return_value=("A", "A.exe", 1)):
        sensor._poll()
    first_since = sensor.snapshot()["since_ts"]
    # 再次轮询相同窗口
    with patch("core.companion.sensors._get_foreground_window",
               return_value=("A", "A.exe", 1)):
        sensor._poll()
    second_since = sensor.snapshot()["since_ts"]
    assert first_since == second_since


def test_active_window_sensor_window_changed_recently_false_after_time():
    """窗口变更超过 30s 后 window_changed_recently 返回 False。"""
    import time
    sensor = ActiveWindowSensor(interval_seconds=2)
    # 模拟 40s 前变更
    sensor._last_change_ts = time.time() - 40
    assert sensor.window_changed_recently() is False
    assert sensor.window_changed_recently(window_seconds=60) is True


def test_active_window_sensor_initial_snapshot_empty():
    """未轮询前 snapshot 应返回空状态。"""
    sensor = ActiveWindowSensor(interval_seconds=2)
    snap = sensor.snapshot()
    assert snap["window_title"] == ""
    assert snap["process_name"] == ""
    assert snap["since_ts"] == 0.0


def test_active_window_sensor_start_stop():
    """start() 和 stop() 应不抛异常。"""
    sensor = ActiveWindowSensor(interval_seconds=2)
    sensor.stop()  # 未 start 时 stop 不抛异常


# === ActivityTracker 工作会话 ===

def test_activity_tracker_work_session_accumulates():
    """工作会话时间应随时间累计（idle<300 时）。"""
    import time
    sensor = ActivityTracker(interval_seconds=30)
    sensor._work_session_start_ts = time.time() - 600  # 10min 前
    with patch("core.companion.sensors._get_last_input_info", return_value=0), \
         patch("core.companion.sensors._get_tick_count", return_value=1000):
        sensor._poll()
    assert sensor.work_session_minutes >= 9  # 约 10min


def test_activity_tracker_work_session_resets_on_5min_idle():
    """idle 超过 5min 时应重置工作会话。"""
    sensor = ActivityTracker(interval_seconds=30)
    sensor.work_session_minutes = 60  # 已工作 60min
    # idle = 310s (> 300)
    with patch("core.companion.sensors._get_last_input_info", return_value=0), \
         patch("core.companion.sensors._get_tick_count", return_value=310000):
        sensor._poll()
    assert sensor.work_session_minutes == 0


def test_activity_tracker_idle_zero_when_same_tick():
    """tick 和 last_input 相同时 idle 为 0。"""
    sensor = ActivityTracker(interval_seconds=30)
    with patch("core.companion.sensors._get_last_input_info", return_value=5000), \
         patch("core.companion.sensors._get_tick_count", return_value=5000):
        sensor._poll()
    assert sensor.idle_seconds == 0


def test_activity_tracker_idle_never_negative():
    """idle_seconds 应为非负值（max(0, ...)）。"""
    sensor = ActivityTracker(interval_seconds=30)
    # tick < last_input（异常情况）
    with patch("core.companion.sensors._get_last_input_info", return_value=10000), \
         patch("core.companion.sensors._get_tick_count", return_value=5000):
        sensor._poll()
    assert sensor.idle_seconds == 0


# === IdleStateTracker 转移 ===

def test_idle_state_tracker_boundary_300():
    """idle_seconds=300 应为 idle（边界 <300 为 active）。"""
    tracker = IdleStateTracker()
    tracker.update(idle_seconds=299)
    assert tracker.idle_state == "active"
    tracker.update(idle_seconds=300)
    assert tracker.idle_state == "idle"


def test_idle_state_tracker_boundary_900():
    """idle_seconds=900 应为 away（边界 <900 为 idle）。"""
    tracker = IdleStateTracker()
    tracker.update(idle_seconds=899)
    assert tracker.idle_state == "idle"
    tracker.update(idle_seconds=900)
    assert tracker.idle_state == "away"


def test_idle_state_tracker_since_ts_updates_on_change():
    """状态变化时应更新 since_ts。"""
    import time
    tracker = IdleStateTracker()
    old_ts = tracker.since_ts
    time.sleep(0.01)
    tracker.update(idle_seconds=600)  # active → idle
    assert tracker.since_ts > old_ts


def test_idle_state_tracker_since_ts_unchanged_on_no_change():
    """状态不变时 since_ts 不变。"""
    tracker = IdleStateTracker()
    old_ts = tracker.since_ts
    tracker.update(idle_seconds=10)  # active → active
    assert tracker.since_ts == old_ts


# === ClipboardSensor 边界 ===

def test_clipboard_sensor_disabled_returns_empty():
    """禁用的剪贴板传感器 snapshot 应返回空 dict。"""
    sensor = ClipboardSensor(enabled=False)
    assert sensor.snapshot() == {}


def test_clipboard_sensor_empty_text_does_not_update():
    """空文本不应更新内部状态。"""
    sensor = ClipboardSensor(enabled=True)
    with patch("core.companion.sensors._get_clipboard_text", return_value=""):
        sensor._poll()
    snap = sensor.snapshot()
    assert snap.get("preview") is None
    assert snap.get("length") == 0


def test_clipboard_sensor_same_text_does_not_update():
    """相同文本不应更新内部状态。"""
    sensor = ClipboardSensor(enabled=True)
    with patch("core.companion.sensors._get_clipboard_text", return_value="hello"):
        sensor._poll()
    first_hash = sensor.snapshot()["hash"]
    with patch("core.companion.sensors._get_clipboard_text", return_value="hello"):
        sensor._poll()
    second_hash = sensor.snapshot()["hash"]
    assert first_hash == second_hash


def test_clipboard_sensor_preview_truncated_to_50():
    """preview 应截断为前 50 个字符。"""
    sensor = ClipboardSensor(enabled=True)
    long_text = "x" * 100
    with patch("core.companion.sensors._get_clipboard_text", return_value=long_text):
        sensor._poll()
    snap = sensor.snapshot()
    assert len(snap["preview"]) == 50
    assert snap["length"] == 100


def test_clipboard_sensor_filters_all_sensitive_keywords():
    """所有敏感关键词都应被过滤。"""
    from core.companion.sensors import SENSITIVE_PATTERN
    for keyword in ["password", "passwd", "secret", "api_key", "api-key",
                    "apiKey", "token", "credential"]:
        assert SENSITIVE_PATTERN.search(f"my_{keyword}=value") is not None


def test_clipboard_sensor_non_sensitive_text_passes():
    """非敏感文本应正常返回 preview。"""
    sensor = ClipboardSensor(enabled=True)
    with patch("core.companion.sensors._get_clipboard_text",
               return_value="正常文本内容"):
        sensor._poll()
    snap = sensor.snapshot()
    assert snap["preview"] == "正常文本内容"


def test_clipboard_sensor_start_does_not_start_when_disabled():
    """禁用的传感器 start() 不应创建 QTimer。"""
    sensor = ClipboardSensor(enabled=False)
    sensor.start()  # 不应抛异常
    assert sensor._timer is None


# === ScreenSensor 边界 ===

def test_screen_sensor_disabled_returns_empty():
    """禁用的 ScreenSensor snapshot 返回空 dict。"""
    sensor = ScreenSensor(enabled=False)
    assert sensor.snapshot() == {}


def test_screen_sensor_capture_does_nothing_when_disabled():
    """禁用时 capture() 不做任何事。"""
    sensor = ScreenSensor(enabled=False)
    sensor.capture()
    assert sensor._frame_b64 == ""
    assert sensor._last_capture_ts == 0.0


def test_screen_sensor_capture_handles_mss_exception():
    """mss 抛异常时应静默降级。"""
    sensor = ScreenSensor(enabled=True)
    with patch("core.companion.sensors.mss.mss", side_effect=Exception("mss error")):
        sensor.capture()
    assert sensor._frame_b64 == ""


def test_screen_sensor_snapshot_when_enabled():
    """启用的 ScreenSensor snapshot 返回 frame_b64 和 captured_at。"""
    sensor = ScreenSensor(enabled=True)
    sensor._frame_b64 = "test_b64"
    sensor._last_capture_ts = 12345.0
    snap = sensor.snapshot()
    assert snap["frame_jpg_b64"] == "test_b64"
    assert snap["captured_at"] == 12345.0


# === build_snapshot 全禁用场景 ===

def test_build_snapshot_all_sensors_disabled():
    """所有传感器禁用时 build_snapshot 仍应返回有效 ContextSnapshot。"""
    aw = ActiveWindowSensor(interval_seconds=2)
    at = ActivityTracker(interval_seconds=30)
    it = IdleStateTracker()
    clip = ClipboardSensor(enabled=False)
    screen = ScreenSensor(enabled=False)
    snap = build_snapshot(
        active_window=aw, activity=at, idle=it,
        clipboard=clip, screen=screen,
        last_greeting_ts="2026-01-01T00:00:00Z",
        last_topic="idle", greeting_count=5,
        local_time="14:30 周二", is_deep_night=False,
    )
    assert snap.timestamp is not None
    assert snap.last_companion_greeting_ts == "2026-01-01T00:00:00Z"
    assert snap.last_companion_topic == "idle"
    assert snap.greeting_count_today == 5
    assert snap.clipboard_preview is None
    assert snap.screen_ocr_text is None


def test_build_snapshot_with_clipboard_and_screen():
    """启用剪贴板和屏幕传感器时 build_snapshot 应聚合其字段。"""
    aw = ActiveWindowSensor(interval_seconds=2)
    at = ActivityTracker(interval_seconds=30)
    it = IdleStateTracker()
    clip = ClipboardSensor(enabled=True)
    with patch("core.companion.sensors._get_clipboard_text", return_value="hello world"):
        clip._poll()
    screen = ScreenSensor(enabled=True)
    screen._frame_b64 = "SCREEN_B64"
    screen._last_capture_ts = 99999.0
    snap = build_snapshot(
        active_window=aw, activity=at, idle=it,
        clipboard=clip, screen=screen,
        last_greeting_ts=None, last_topic=None, greeting_count=0,
        local_time="14:30", is_deep_night=True,
    )
    assert snap.clipboard_preview == "hello world"
    assert snap.screen_ocr_text == "SCREEN_B64"
    assert snap.is_deep_night is True


def test_build_snapshot_timestamp_is_iso_format():
    """snapshot.timestamp 应为 ISO8601 格式且带 Z 后缀。"""
    aw = ActiveWindowSensor(interval_seconds=2)
    at = ActivityTracker(interval_seconds=30)
    it = IdleStateTracker()
    snap = build_snapshot(
        active_window=aw, activity=at, idle=it,
        clipboard=None, screen=None,
        last_greeting_ts=None, last_topic=None, greeting_count=0,
        local_time="14:30", is_deep_night=False,
    )
    assert snap.timestamp.endswith("Z")


# === win32 抽象层 ===

def test_get_foreground_window_returns_empty_when_win32_none():
    """win32gui/win32process 为 None 时返回 ('', '', 0)。"""
    with patch("core.companion.sensors.win32gui", None), \
         patch("core.companion.sensors.win32process", None):
        result = _get_foreground_window()
    assert result == ("", "", 0)


def test_get_foreground_window_handles_exception():
    """win32 API 抛异常时返回 ('', '', 0)。"""
    mock_gui = MagicMock()
    mock_gui.GetForegroundWindow.side_effect = Exception("API error")
    with patch("core.companion.sensors.win32gui", mock_gui), \
         patch("core.companion.sensors.win32process", MagicMock()):
        result = _get_foreground_window()
    assert result == ("", "", 0)


def test_get_clipboard_text_returns_empty_when_win32_none():
    """win32clipboard 为 None 时返回空串。"""
    with patch("core.companion.sensors.win32clipboard", None):
        result = _get_clipboard_text()
    assert result == ""


def test_get_clipboard_text_handles_exception():
    """OpenClipboard 抛异常时返回空串。"""
    mock_clip = MagicMock()
    mock_clip.OpenClipboard.side_effect = Exception("clipboard locked")
    with patch("core.companion.sensors.win32clipboard", mock_clip):
        result = _get_clipboard_text()
    assert result == ""


def test_frame_to_b64_produces_valid_base64():
    """_frame_to_b64 应返回合法的 base64 字符串。"""
    import base64
    from PIL import Image
    img = Image.new("RGB", (10, 10), color="red")
    result = _frame_to_b64(img)
    decoded = base64.b64decode(result)
    # JPEG 文件以 FF D8 开头
    assert decoded[:2] == b"\xff\xd8"


def test_frame_to_b64_returns_ascii_string():
    """_frame_to_b64 返回值应为纯 ASCII 字符串。"""
    from PIL import Image
    img = Image.new("RGB", (5, 5), color="blue")
    result = _frame_to_b64(img)
    assert isinstance(result, str)
    assert result.isascii()
