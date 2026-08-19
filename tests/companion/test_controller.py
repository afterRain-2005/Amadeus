"""CompanionController 单元测试：缓存/冷却/异常容灾/工具函数。

controller.py 是 companion 子系统的编排核心，聚合 scheduler + evaluator + storage。

测试覆盖：

- __init__ 的 init_schema 调用与容灾

- on_user_message 更新冷却时间戳

- handle_signal 的三重门控：用户冷却 / should_consider / 全局冷却

- handle_signal 的 storage 异常容灾（greeting_count_today / last_greeting_ts 异常）

- _speak 的 record_greeting 时序（route_and_send 成功后才写）

- _speak 的 storage 异常不影响回复

- _parse_iso_to_epoch 工具函数

- _get_config / _get_soul_md 缓存

- invalidate_cache 清除缓存

- start / stop 无操作

"""

from __future__ import annotations

import time

from datetime import datetime, timezone

from unittest.mock import patch, MagicMock

import pytest

from core.companion.controller import CompanionController

from core.companion.sensors import ContextSnapshot

def _snap(**kwargs) -> ContextSnapshot:

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

def _make_controller(**llm_overrides) -> CompanionController:

    """工厂：构造一个高频 enabled 控制器。"""

    llm = {"endpoint": "http://x", "api_key": "k", "model": "m"}

    llm.update(llm_overrides)

    return CompanionController(config={

        "enabled": True, "frequency": "high", "daily_limit": 30,

        "quiet_hours": {"start": "23:00", "end": "08:00"},

        "sensors": {},

    }, llm_config=llm)

def _patch_all_storage(**overrides):

    """批量 patch storage 函数，返回 mock dict。"""

    defaults = {

        "record_greeting": MagicMock(return_value=1),

        "last_greeting_ts": MagicMock(return_value=None),

        "greeting_count_today": MagicMock(return_value=0),

    }

    defaults.update(overrides)

    return defaults

# === __init__ ===

def test_init_calls_init_schema():

    """构造时应调用 storage.init_schema。"""

    with patch("core.companion.controller.storage.init_schema") as mock_init:

        _make_controller()

    mock_init.assert_called_once()

def test_init_survives_storage_failure():

    """init_schema 抛异常时不应崩溃（companion 永不影响主流程）。"""

    with patch("core.companion.controller.storage.init_schema", side_effect=Exception("DB error")):

        ctrl = _make_controller()

    assert ctrl is not None

    assert ctrl.scheduler is not None

def test_init_reads_llm_config():

    """构造时应从 llm_config 读取 endpoint/api_key/model。"""

    ctrl = _make_controller(endpoint="http://my-api", api_key="secret", model="gpt-4")

    assert ctrl.llm_endpoint == "http://my-api"

    assert ctrl.llm_api_key == "secret"

    assert ctrl.llm_model == "gpt-4"

def test_init_defaults_empty_llm_config():

    """llm_config 为空时应回退空字符串。"""

    ctrl = _make_controller(endpoint="", api_key="", model="")

    assert ctrl.llm_endpoint == ""

    assert ctrl.llm_api_key == ""

    assert ctrl.llm_model == ""

def test_init_cache_fields_none():

    """缓存字段初始应为 None。"""

    ctrl = _make_controller()

    assert ctrl._cached_config is None

    assert ctrl._cached_soul_md is None

    assert ctrl._last_user_msg_ts is None

# === on_user_message ===

def test_on_user_message_sets_timestamp():

    ctrl = _make_controller()

    assert ctrl._last_user_msg_ts is None

    ctrl.on_user_message()

    assert ctrl._last_user_msg_ts is not None

    assert abs(time.time() - ctrl._last_user_msg_ts) < 1

def test_on_user_message_updates_on_repeated_calls():

    ctrl = _make_controller()

    ctrl.on_user_message()

    ts1 = ctrl._last_user_msg_ts

    time.sleep(0.01)

    ctrl.on_user_message()

    ts2 = ctrl._last_user_msg_ts

    assert ts2 > ts1

# === handle_signal: 用户对话冷却 ===

def test_handle_signal_user_cooldown_blocks():

    """用户刚发消息（<5min）时 handle_signal 应直接返回。"""

    ctrl = _make_controller()

    ctrl.on_user_message()  # 设置 _last_user_msg_ts

    mocks = _patch_all_storage()

    with patch("core.companion.storage.record_greeting", mocks["record_greeting"]), \
         patch("core.companion.storage.last_greeting_ts", mocks["last_greeting_ts"]), \
         patch("core.companion.storage.greeting_count_today", mocks["greeting_count_today"]), \
         patch("core.backend_router.route_and_send") as mock_router:

        ctrl.handle_signal(_snap(idle_seconds=1000), local_hour=14)

    mock_router.assert_not_called()

    mocks["record_greeting"].assert_not_called()

def test_handle_signal_user_cooldown_expired_allows():

    """用户消息冷却过期后（模拟 >5min 前）应允许。"""

    ctrl = _make_controller()

    # 设置为 6 分钟前

    ctrl._last_user_msg_ts = time.time() - 360

    mocks = _patch_all_storage()

    with patch("core.companion.storage.record_greeting", mocks["record_greeting"]), \
         patch("core.companion.storage.last_greeting_ts", mocks["last_greeting_ts"]), \
         patch("core.companion.storage.greeting_count_today", mocks["greeting_count_today"]), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")):

        ctrl.handle_signal(_snap(idle_seconds=1000), local_hour=14)

    mocks["record_greeting"].assert_called_once()

# === handle_signal: scheduler should_consider 门控 ===

def test_handle_signal_disabled_blocks():

    """disabled 控制器不应触发。"""

    ctrl = CompanionController(config={"enabled": False}, llm_config={})

    mocks = _patch_all_storage()

    with patch("core.companion.storage.record_greeting", mocks["record_greeting"]), \
         patch("core.companion.storage.last_greeting_ts", mocks["last_greeting_ts"]), \
         patch("core.companion.storage.greeting_count_today", mocks["greeting_count_today"]):

        ctrl.handle_signal(_snap(idle_seconds=1000), local_hour=14)

    mocks["record_greeting"].assert_not_called()

def test_handle_signal_quiet_hours_blocks():

    """静音时段（非 away）不触发。"""

    ctrl = _make_controller()

    mocks = _patch_all_storage()

    with patch("core.companion.storage.record_greeting", mocks["record_greeting"]), \
         patch("core.companion.storage.last_greeting_ts", mocks["last_greeting_ts"]), \
         patch("core.companion.storage.greeting_count_today", mocks["greeting_count_today"]):

        ctrl.handle_signal(_snap(idle_seconds=1000), local_hour=2)  # 02:00 静音

    mocks["record_greeting"].assert_not_called()

def test_handle_signal_daily_limit_blocks():

    """已达每日上限不触发。"""

    ctrl = _make_controller()

    mocks = _patch_all_storage(greeting_count_today=MagicMock(return_value=30))

    with patch("core.companion.storage.record_greeting", mocks["record_greeting"]), \
         patch("core.companion.storage.last_greeting_ts", mocks["last_greeting_ts"]), \
         patch("core.companion.storage.greeting_count_today", mocks["greeting_count_today"]):

        ctrl.handle_signal(_snap(idle_seconds=1000), local_hour=14)

    mocks["record_greeting"].assert_not_called()

# === handle_signal: 全局冷却 ===

def test_handle_signal_global_cooldown_blocks():

    """全局冷却未过期（<10min）时不触发。"""

    ctrl = _make_controller()

    recent_ts = datetime.now(timezone.utc).timestamp() - 300  # 5min 前

    recent_iso = datetime.fromtimestamp(recent_ts, tz=timezone.utc).isoformat()

    mocks = _patch_all_storage(last_greeting_ts=MagicMock(return_value=recent_iso))

    with patch("core.companion.storage.record_greeting", mocks["record_greeting"]), \
         patch("core.companion.storage.last_greeting_ts", mocks["last_greeting_ts"]), \
         patch("core.companion.storage.greeting_count_today", mocks["greeting_count_today"]):

        ctrl.handle_signal(_snap(idle_seconds=1000), local_hour=14)

    mocks["record_greeting"].assert_not_called()

def test_handle_signal_global_cooldown_expired_allows():

    """全局冷却过期后（>10min）应允许。"""

    ctrl = _make_controller()

    old_ts = datetime.now(timezone.utc).timestamp() - 660  # 11min 前

    old_iso = datetime.fromtimestamp(old_ts, tz=timezone.utc).isoformat()

    mocks = _patch_all_storage(last_greeting_ts=MagicMock(return_value=old_iso))

    with patch("core.companion.storage.record_greeting", mocks["record_greeting"]), \
         patch("core.companion.storage.last_greeting_ts", mocks["last_greeting_ts"]), \
         patch("core.companion.storage.greeting_count_today", mocks["greeting_count_today"]), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")):

        ctrl.handle_signal(_snap(idle_seconds=1000), local_hour=14)

    mocks["record_greeting"].assert_called_once()

# === handle_signal: storage 异常容灾 ===

def test_handle_signal_greeting_count_today_exception_degrades_to_zero():

    """greeting_count_today 抛异常时应降级为 0，不崩溃。"""

    ctrl = _make_controller()

    with patch("core.companion.storage.greeting_count_today", side_effect=Exception("DB locked")), \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.record_greeting", return_value=1), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router:

        ctrl.handle_signal(_snap(idle_seconds=1000), local_hour=14)

    mock_router.assert_called_once()

def test_handle_signal_last_greeting_ts_exception_degrades_to_none():

    """last_greeting_ts 抛异常时应降级为 None，不崩溃。"""

    ctrl = _make_controller()

    with patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.companion.storage.last_greeting_ts", side_effect=Exception("DB locked")), \
         patch("core.companion.storage.record_greeting", return_value=1), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router:

        ctrl.handle_signal(_snap(idle_seconds=1000), local_hour=14)

    mock_router.assert_called_once()

def test_handle_signal_evaluator_returns_none_no_speak():

    """评估器返回 None 时不应调 route_and_send。"""

    ctrl = _make_controller()

    with patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.controller.Evaluator.evaluate", return_value=None), \
         patch("core.backend_router.route_and_send") as mock_router:

        ctrl.handle_signal(_snap(idle_seconds=10), local_hour=14)

    mock_router.assert_not_called()

# === _speak: record_greeting 时序 ===

def test_speak_records_greeting_after_route_success():

    """route_and_send 成功后才调 record_greeting。"""

    ctrl = _make_controller()

    call_order = []

    def fake_router(**kwargs):

        call_order.append("route_and_send")

        return ("reply", "chat")

    def fake_record(text, topic, emotion):

        call_order.append("record_greeting")

        return 1

    with patch("core.companion.storage.record_greeting", side_effect=fake_record), \
         patch("core.backend_router.route_and_send", side_effect=fake_router), \
         patch.object(ctrl, "_get_config", return_value={}), \
         patch.object(ctrl, "_get_soul_md", return_value="soul"):

        ctrl.handle_signal(_snap(idle_seconds=1000), local_hour=14)

    assert call_order == ["route_and_send", "record_greeting"]

def test_speak_storage_failure_does_not_block_reply():

    """record_greeting 抛异常时不应影响回复和 on_finished 回调。"""

    ctrl = _make_controller()

    on_finished = MagicMock()

    with patch("core.companion.storage.record_greeting", side_effect=Exception("DB error")), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")), \
         patch.object(ctrl, "_get_config", return_value={}), \
         patch.object(ctrl, "_get_soul_md", return_value="soul"):

        ctrl.handle_signal(

            _snap(idle_seconds=1000), local_hour=14,

            on_finished=on_finished,

        )

    on_finished.assert_called_once_with("reply")

def test_speak_route_failure_no_record():

    """route_and_send 抛异常时不应调 record_greeting。"""

    ctrl = _make_controller()

    with patch("core.companion.storage.record_greeting") as mock_record, \
         patch("core.backend_router.route_and_send", side_effect=Exception("LLM error")), \
         patch.object(ctrl, "_get_config", return_value={}), \
         patch.object(ctrl, "_get_soul_md", return_value="soul"):

        ctrl.handle_signal(_snap(idle_seconds=1000), local_hour=14)

    mock_record.assert_not_called()

def test_speak_passes_on_delta_and_on_status():

    """on_delta / on_status 回调应透传给 route_and_send。"""

    ctrl = _make_controller()

    on_delta = MagicMock()

    on_status = MagicMock()

    with patch("core.companion.storage.record_greeting", return_value=1), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router, \
         patch.object(ctrl, "_get_config", return_value={}), \
         patch.object(ctrl, "_get_soul_md", return_value="soul"):

        ctrl.handle_signal(

            _snap(idle_seconds=1000), local_hour=14,

            on_delta=on_delta, on_status=on_status,

        )

    _, kwargs = mock_router.call_args

    assert kwargs["on_delta"] is on_delta

    assert kwargs["on_status"] is on_status

def test_speak_uses_companion_system_role():

    """route_and_send 应收到 system_role='companion'。"""

    ctrl = _make_controller()

    with patch("core.companion.storage.record_greeting", return_value=1), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router, \
         patch.object(ctrl, "_get_config", return_value={}), \
         patch.object(ctrl, "_get_soul_md", return_value="soul"):

        ctrl.handle_signal(_snap(idle_seconds=1000), local_hour=14)

    _, kwargs = mock_router.call_args

    assert kwargs["system_role"] == "companion"

    assert kwargs["skip_history"] is True

def test_speak_injects_pass_through_prompt():

    """route_and_send 应收到 inject_system_prompt 含 decision.text。"""

    ctrl = _make_controller()

    with patch("core.companion.storage.record_greeting", return_value=1), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router, \
         patch.object(ctrl, "_get_config", return_value={}), \
         patch.object(ctrl, "_get_soul_md", return_value="soul"):

        ctrl.handle_signal(_snap(idle_seconds=1000), local_hour=14)

    _, kwargs = mock_router.call_args

    inject = kwargs["inject_system_prompt"]

    assert "盯着屏幕发呆" in inject  # L1 idle 模板文本

# === _parse_iso_to_epoch ===

def test_parse_iso_to_epoch_valid_utc():

    iso = "2026-08-16T10:00:00+00:00"

    result = CompanionController._parse_iso_to_epoch(iso)

    assert isinstance(result, float)

    assert result > 0

def test_parse_iso_to_epoch_with_z_suffix():

    iso = "2026-08-16T10:00:00Z"

    result = CompanionController._parse_iso_to_epoch(iso)

    assert isinstance(result, float)

    assert result > 0

def test_parse_iso_to_epoch_invalid_string():

    assert CompanionController._parse_iso_to_epoch("not-a-date") == 0.0

def test_parse_iso_to_epoch_empty_string():

    """空字符串应抛 ValueError 被 except 捕获，返回 0.0。"""

    # fromisoformat("") 会抛 ValueError

    assert CompanionController._parse_iso_to_epoch("") == 0.0

def test_parse_iso_to_epoch_none_raises():

    """None 传入应抛 TypeError 被 except 捕获，返回 0.0。"""

    # str.replace 在 None 上会抛 TypeError

    assert CompanionController._parse_iso_to_epoch(None) == 0.0  # type: ignore

# === _get_config / _get_soul_md 缓存 ===

def test_get_config_caches_result():

    """_get_config 首次调用后缓存，第二次不读磁盘。"""

    ctrl = _make_controller()

    with patch("core.storage.load_config", return_value={"key": "val"}) as mock_load:

        result1 = ctrl._get_config()

        result2 = ctrl._get_config()

    assert result1 == {"key": "val"}

    assert result2 == {"key": "val"}

    mock_load.assert_called_once()

def test_get_soul_md_caches_from_file():

    """_get_soul_md 从 SOUL.md 文件读取后缓存。"""

    ctrl = _make_controller()

    fake_path = MagicMock()

    fake_path.exists.return_value = True

    fake_path.read_text.return_value = "soul content"

    fake_app_dir = MagicMock()

    fake_app_dir.__truediv__ = lambda self, other: fake_path

    with patch("core.storage.APP_DIR", fake_app_dir):

        result1 = ctrl._get_soul_md()

        result2 = ctrl._get_soul_md()

    assert result1 == "soul content"

    assert result2 == "soul content"

    fake_path.read_text.assert_called_once()

def test_get_soul_md_fallback_to_character_personality():

    """SOUL.md 不存在时回退到 KURISU_PERSONALITY。"""

    ctrl = _make_controller()

    fake_app_dir = MagicMock()

    fake_soul_path = MagicMock()

    fake_soul_path.exists.return_value = False

    fake_app_dir.__truediv__ = lambda self, other: fake_soul_path

    fake_character = MagicMock()

    fake_character.personality = "fallback personality"

    with patch("core.storage.APP_DIR", fake_app_dir), \
         patch("config.get_character_by_id", return_value=fake_character):

        result = ctrl._get_soul_md()

    assert result == "fallback personality"

def test_get_soul_md_fallback_when_character_none():

    """SOUL.md 不存在且 get_character_by_id 返回 None 时返回空串。"""

    ctrl = _make_controller()

    fake_soul_path = MagicMock()

    fake_soul_path.exists.return_value = False

    fake_app_dir = MagicMock()

    fake_app_dir.__truediv__ = lambda self, other: fake_soul_path

    with patch("core.storage.APP_DIR", fake_app_dir), \
         patch("config.get_character_by_id", return_value=None):

        result = ctrl._get_soul_md()

    assert result == ""

def test_invalidate_cache_clears_config():

    """invalidate_cache 应清除 config 缓存。"""

    ctrl = _make_controller()

    with patch("core.storage.load_config", return_value={"v": 1}):

        ctrl._get_config()

    assert ctrl._cached_config is not None

    ctrl.invalidate_cache()

    assert ctrl._cached_config is None

def test_invalidate_cache_clears_soul_md():

    """invalidate_cache 应清除 soul_md 缓存。"""

    ctrl = _make_controller()

    fake_path = MagicMock()

    fake_path.exists.return_value = True

    fake_path.read_text.return_value = "soul"

    fake_app_dir = MagicMock()

    fake_app_dir.__truediv__ = lambda self, other: fake_path

    with patch("core.storage.APP_DIR", fake_app_dir):

        ctrl._get_soul_md()

    assert ctrl._cached_soul_md is not None

    ctrl.invalidate_cache()

    assert ctrl._cached_soul_md is None

def test_invalidate_cache_forces_reload():

    """invalidate_cache 后下次调用应重新读取。"""

    ctrl = _make_controller()

    with patch("core.storage.load_config", return_value={"v": 1}) as mock_load:

        ctrl._get_config()

        ctrl.invalidate_cache()

        ctrl._get_config()

    assert mock_load.call_count == 2

# === start / stop ===

def test_start_is_noop():

    """start() 应是空操作，不抛异常。"""

    ctrl = _make_controller()

    ctrl.start()  # 不应抛异常

def test_stop_is_noop():

    """stop() 应是空操作，不抛异常。"""

    ctrl = _make_controller()

    ctrl.stop()  # 不应抛异常

def test_start_with_parent_is_noop():

    ctrl = _make_controller()

    parent = MagicMock()

    ctrl.start(parent=parent)  # 不应抛异常

# === handle_signal: signal_type 传递 ===

def test_handle_signal_passes_idle_state_as_signal_type():

    """handle_signal 应把 snapshot.idle_state 作为 signal_type 传给 evaluator。"""

    ctrl = _make_controller()

    with patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.controller.Evaluator.evaluate") as mock_eval, \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")):

        ctrl.handle_signal(_snap(idle_state="idle"), local_hour=14)

    _, kwargs = mock_eval.call_args

    assert kwargs["signal_type"] == "idle"

def test_handle_signal_default_signal_type_when_idle_state_empty():

    """idle_state 为空字符串时应传 'default'。"""

    ctrl = _make_controller()

    with patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.controller.Evaluator.evaluate") as mock_eval, \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")):

        ctrl.handle_signal(_snap(idle_state=""), local_hour=14)

    _, kwargs = mock_eval.call_args

    assert kwargs["signal_type"] == "default"
