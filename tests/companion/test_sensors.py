"""5 传感器 + ContextSnapshot 测试。所有 win32/mss 调用均 mock。"""
from unittest.mock import patch, MagicMock

from core.companion.sensors import (
    ContextSnapshot, ActiveWindowSensor, ActivityTracker,
    IdleStateTracker, ClipboardSensor, ScreenSensor, build_snapshot,
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
