"""端到端集成测试：snapshot → evaluate → speak → route_and_send。"""
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from core.companion.controller import CompanionController
from core.companion.sensors import ContextSnapshot


def _snap(**kwargs):
    defaults = dict(
        timestamp="2026-08-16T10:00:00Z", local_time="14:30 周二",
        is_deep_night=False, idle_seconds=10, work_session_minutes=5,
        idle_state="active", active_window_title="main.py - Code",
        active_process="Code.exe", window_changed_recently=False,
        last_companion_greeting_ts=None, last_companion_topic=None,
        greeting_count_today=0,
    )
    defaults.update(kwargs)
    return ContextSnapshot(**defaults)


def test_controller_idle_over_15min_triggers_template_and_calls_storage_and_router():
    """空闲 16min → L1 模板命中 → 写 storage + 调 route_and_send。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {"active_window": True, "activity": True, "idle": True,
                    "clipboard": False, "screen": False},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    snap = _snap(idle_seconds=1000)  # >900 → L1 命中

    with patch("core.companion.storage.record_greeting", return_value=1) as mock_record, \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router:
        ctrl.handle_signal(snap, local_hour=14)

    mock_record.assert_called_once()
    mock_router.assert_called_once()
    # 验证 route_and_send 收到 companion 模式参数
    _, kwargs = mock_router.call_args
    assert kwargs["system_role"] == "companion"
    assert kwargs["skip_history"] is True
    assert kwargs["inject_system_prompt"] is not None
    assert "盯着屏幕发呆" in kwargs["input_text"]


def test_controller_calls_finished_callback_with_full_reply():
    """route_and_send 返回完整回复后，应通知表达层显示气泡。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    snap = _snap(idle_seconds=1000)
    on_finished = MagicMock()

    with patch("core.companion.storage.record_greeting", return_value=1), \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send",
               return_value=("[emotion:smile]\n别盯着屏幕发呆啦\n===\nねえ", "chat")):
        ctrl.handle_signal(snap, local_hour=14, on_finished=on_finished)

    on_finished.assert_called_once_with("[emotion:smile]\n别盯着屏幕发呆啦\n===\nねえ")


def test_controller_scheduler_blocks_in_quiet_hours():
    """静音时段（非 away）不触发。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={})

    snap = _snap(idle_seconds=1000)  # L1 本应命中

    with patch("core.companion.storage.record_greeting") as mock_record, \
         patch("core.backend_router.route_and_send") as mock_router:
        ctrl.handle_signal(snap, local_hour=2)  # 02:30 静音时段

    mock_record.assert_not_called()
    mock_router.assert_not_called()


def test_controller_disabled_does_nothing():
    ctrl = CompanionController(config={"enabled": False}, llm_config={})
    snap = _snap(idle_seconds=1000)
    with patch("core.companion.storage.record_greeting") as mock_record:
        ctrl.handle_signal(snap, local_hour=14)
    mock_record.assert_not_called()


def test_controller_llm_decision_path():
    """L1 不命中时走 LLM 决策路径。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    snap = _snap(idle_seconds=10)  # L1 不命中

    llm_resp = {"should_speak": True, "text": "在写代码啊", "emotion": "neutral", "topic": "work"}
    with patch("core.companion.evaluator._call_llm", return_value=llm_resp), \
         patch("core.companion.storage.record_greeting", return_value=1), \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router:
        ctrl.handle_signal(snap, local_hour=14)

    _, kwargs = mock_router.call_args
    assert kwargs["input_text"] == "在写代码啊"
