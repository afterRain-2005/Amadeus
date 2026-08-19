"""端到端集成测试：snapshot → evaluate → speak → route_and_send。"""
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone

from core.companion.controller import CompanionController
from core.companion.sensors import ContextSnapshot
from core.companion.prompts import COMPANION_TO_LIVE2D_EMOTION, COMPANION_EMOTION_MOTION


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
    """空闲 16min → L1 模板命中 → 写 storage + 调 route_and_send。

    C-03 修复后：record_greeting 在 route_and_send 成功后才调用。
    """
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


# === Live2D 表情/动作映射测试（C-01, C-02） ===

def test_companion_emotion_maps_to_live2d_emotion():
    """companion 内部情绪应正确映射为 Live2D 情绪。"""
    assert COMPANION_TO_LIVE2D_EMOTION["idle"] == "neutral"
    assert COMPANION_TO_LIVE2D_EMOTION["sleepy"] == "sad"
    assert COMPANION_TO_LIVE2D_EMOTION["concern"] == "sad"
    assert COMPANION_TO_LIVE2D_EMOTION["tease"] == "angry"
    assert COMPANION_TO_LIVE2D_EMOTION["happy"] == "smile"
    assert COMPANION_TO_LIVE2D_EMOTION["neutral"] == "neutral"


def test_companion_emotion_maps_to_live2d_motion():
    """companion 内部情绪应正确映射为 Live2D 动作。"""
    assert COMPANION_EMOTION_MOTION["idle"] == "thinking"
    assert COMPANION_EMOTION_MOTION["sleepy"] == "sad"
    assert COMPANION_EMOTION_MOTION["concern"] == "sad"
    assert COMPANION_EMOTION_MOTION["tease"] == "angry"
    assert COMPANION_EMOTION_MOTION["happy"] == "smile"
    assert COMPANION_EMOTION_MOTION["neutral"] == "neutral"


def test_all_companion_emotions_have_live2d_mapping():
    """所有 prompts 模板中使用的情绪都应有 Live2D 映射。"""
    from core.companion.prompts import KURISU_PROACTIVE_TEMPLATES
    template_emotions = {tpl["emotion"] for tpl in KURISU_PROACTIVE_TEMPLATES}
    for emotion in template_emotions:
        assert emotion in COMPANION_TO_LIVE2D_EMOTION, \
            f"模板情绪 '{emotion}' 缺少 Live2D 映射"
        assert emotion in COMPANION_EMOTION_MOTION, \
            f"模板情绪 '{emotion}' 缺少 Live2D 动作映射"


def test_all_live2d_emotions_are_valid():
    """映射后的 Live2D 情绪必须是 emotion_parser 可识别的。"""
    valid_live2d = {"neutral", "blush", "angry", "smile", "sad"}
    for companion_em, live2d_em in COMPANION_TO_LIVE2D_EMOTION.items():
        assert live2d_em in valid_live2d, \
            f"companion '{companion_em}' 映射到无效的 Live2D 情绪 '{live2d_em}'"


def test_all_live2d_motions_are_valid():
    """映射后的 Live2D 动作必须在 live2d_page.html MOTIONS 中存在。"""
    valid_motions = {"neutral", "smile", "blush", "angry", "sad", "thinking"}
    for companion_em, motion in COMPANION_EMOTION_MOTION.items():
        assert motion in valid_motions, \
            f"companion '{companion_em}' 映射到无效的 Live2D 动作 '{motion}'"


def test_controller_record_greeting_not_called_when_route_fails():
    """C-03 验证：route_and_send 失败时不应记录 greeting。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    snap = _snap(idle_seconds=1000)

    with patch("core.companion.storage.record_greeting", return_value=1) as mock_record, \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send", side_effect=Exception("LLM error")):
        ctrl.handle_signal(snap, local_hour=14)

    # route_and_send 失败，record_greeting 不应被调用
    mock_record.assert_not_called()


# === 用户对话冷却 ===

def test_controller_user_message_cooldown_blocks_greeting():
    """用户刚发消息（<5min）时不应触发 companion 问候。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    ctrl.on_user_message()  # 用户刚发消息
    snap = _snap(idle_seconds=1000)

    with patch("core.companion.storage.record_greeting") as mock_record, \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send") as mock_router:
        ctrl.handle_signal(snap, local_hour=14)

    mock_record.assert_not_called()
    mock_router.assert_not_called()


def test_controller_user_message_cooldown_expired_allows():
    """用户消息冷却过期后应允许 companion 问候。"""
    import time
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    # 模拟 6min 前发的消息
    ctrl._last_user_msg_ts = time.time() - 360
    snap = _snap(idle_seconds=1000)

    with patch("core.companion.storage.record_greeting", return_value=1) as mock_record, \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")):
        ctrl.handle_signal(snap, local_hour=14)

    mock_record.assert_called_once()


# === 全局冷却 ===

def test_controller_global_cooldown_blocks_greeting():
    """全局冷却未过期（<10min）时不触发。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    recent_ts = datetime.now(timezone.utc).timestamp() - 300  # 5min 前
    recent_iso = datetime.fromtimestamp(recent_ts, tz=timezone.utc).isoformat()
    snap = _snap(idle_seconds=1000)

    with patch("core.companion.storage.record_greeting") as mock_record, \
         patch("core.companion.storage.last_greeting_ts", return_value=recent_iso), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send") as mock_router:
        ctrl.handle_signal(snap, local_hour=14)

    mock_record.assert_not_called()
    mock_router.assert_not_called()


# === storage 异常容灾 ===

def test_controller_greeting_count_today_exception_degrades():
    """greeting_count_today 抛异常时应降级为 0，不崩溃。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    snap = _snap(idle_seconds=1000)

    with patch("core.companion.storage.greeting_count_today", side_effect=Exception("DB locked")), \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.record_greeting", return_value=1), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router:
        ctrl.handle_signal(snap, local_hour=14)

    mock_router.assert_called_once()  # 降级后仍应触发


def test_controller_last_greeting_ts_exception_degrades():
    """last_greeting_ts 抛异常时应降级为 None，不崩溃。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    snap = _snap(idle_seconds=1000)

    with patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.companion.storage.last_greeting_ts", side_effect=Exception("DB locked")), \
         patch("core.companion.storage.record_greeting", return_value=1), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router:
        ctrl.handle_signal(snap, local_hour=14)

    mock_router.assert_called_once()


def test_controller_record_greeting_exception_does_not_block_reply():
    """record_greeting 抛异常时不应影响回复。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    snap = _snap(idle_seconds=1000)
    on_finished = MagicMock()

    with patch("core.companion.storage.record_greeting", side_effect=Exception("DB error")), \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")):
        ctrl.handle_signal(snap, local_hour=14, on_finished=on_finished)

    on_finished.assert_called_once_with("reply")


# === 缓存 ===

def test_controller_cache_invalidated_on_demand():
    """invalidate_cache 后下次 _get_config 应重新读磁盘。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    with patch("core.storage.load_config", return_value={"v": 1}) as mock_load:
        ctrl._get_config()
        ctrl.invalidate_cache()
        ctrl._get_config()

    assert mock_load.call_count == 2


# === 多模板命中场景 ===

def test_controller_sleepy_template_triggers():
    """深夜工作超 30min → sleepy 模板命中。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    # 注意：02:30 在静音时段，但 sleepy 条件需要 is_deep_night
    # 静音时段非 away 会被阻断，所以用 21:00 测（非静音但 is_deep_night=False）
    # 用 23:01 的场景会进静音，用 22:00 测非静音 + is_deep_night=True
    snap = _snap(is_deep_night=True, work_session_minutes=45, local_time="22:00 周二")

    with patch("core.companion.storage.record_greeting", return_value=1) as mock_record, \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router:
        ctrl.handle_signal(snap, local_hour=22.0)

    mock_router.assert_called_once()
    _, kwargs = mock_router.call_args
    assert "22:00" in kwargs["input_text"] or "睡觉" in kwargs["input_text"]


def test_controller_concern_template_triggers():
    """工作超 2h → concern 模板命中。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    snap = _snap(work_session_minutes=130)

    with patch("core.companion.storage.record_greeting", return_value=1) as mock_record, \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router:
        ctrl.handle_signal(snap, local_hour=14)

    mock_router.assert_called_once()
    _, kwargs = mock_router.call_args
    assert "130" in kwargs["input_text"] or "颈椎" in kwargs["input_text"]


def test_controller_tease_template_triggers():
    """窗口刚切换 + 今日 0 问候 → tease 模板命中。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    snap = _snap(window_changed_recently=True, greeting_count_today=0)

    with patch("core.companion.storage.record_greeting", return_value=1) as mock_record, \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router:
        ctrl.handle_signal(snap, local_hour=14)

    mock_router.assert_called_once()
    _, kwargs = mock_router.call_args
    assert "摸鱼" in kwargs["input_text"] or "切换窗口" in kwargs["input_text"]


def test_controller_away_long_template_triggers():
    """离开超 1h → away_long 模板命中。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    snap = _snap(idle_state="away", idle_seconds=3700)

    with patch("core.companion.storage.record_greeting", return_value=1) as mock_record, \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router:
        ctrl.handle_signal(snap, local_hour=14)

    mock_router.assert_called_once()
    _, kwargs = mock_router.call_args
    assert "还在吗" in kwargs["input_text"] or "很久" in kwargs["input_text"]


# === LLM 路径端到端 ===

def test_controller_llm_fallback_when_l1_misses_and_llm_fails():
    """L1 不命中 + LLM 失败 → fallback_template。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    snap = _snap(idle_seconds=10, work_session_minutes=5)

    with patch("core.companion.evaluator._call_llm", side_effect=OSError("timeout")), \
         patch("core.companion.storage.record_greeting", return_value=1), \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router:
        ctrl.handle_signal(snap, local_hour=14)

    mock_router.assert_called_once()
    _, kwargs = mock_router.call_args
    assert "在忙什么呢" in kwargs["input_text"]


def test_controller_llm_returns_should_speak_false_no_greeting():
    """LLM 返回 should_speak=false 时不触发问候。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    snap = _snap(idle_seconds=10)
    llm_resp = {"should_speak": False, "text": "", "emotion": "", "topic": ""}

    with patch("core.companion.evaluator._call_llm", return_value=llm_resp), \
         patch("core.companion.storage.record_greeting") as mock_record, \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send") as mock_router:
        ctrl.handle_signal(snap, local_hour=14)

    mock_record.assert_not_called()
    mock_router.assert_not_called()


# === 多次问候序列 ===

def test_controller_multiple_greetings_in_sequence():
    """连续两次 handle_signal：第一次触发，第二次被全局冷却阻断。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    snap = _snap(idle_seconds=1000)

    # 第一次：触发
    recent_iso = datetime.now(timezone.utc).isoformat()
    with patch("core.companion.storage.record_greeting", return_value=1) as mock_record, \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")):
        ctrl.handle_signal(snap, local_hour=14)
    assert mock_record.call_count == 1

    # 第二次：被全局冷却阻断（last_greeting_ts 返回当前时间）
    with patch("core.companion.storage.record_greeting", return_value=1) as mock_record2, \
         patch("core.companion.storage.last_greeting_ts", return_value=recent_iso), \
         patch("core.companion.storage.greeting_count_today", return_value=1), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router2:
        ctrl.handle_signal(snap, local_hour=14)
    mock_record2.assert_not_called()
    mock_router2.assert_not_called()
