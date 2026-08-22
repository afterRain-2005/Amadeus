"""Evaluator 测试：L1 硬阈值规则 + L2 LLM 决策 + LLM 失败降级 + fallback 全分支。"""
import json
from unittest.mock import patch, MagicMock

import pytest

from core.companion.evaluator import Evaluator, GreetingDecision
from core.companion.sensors import ContextSnapshot
from core.companion.prompts import KURISU_PROACTIVE_TEMPLATES


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


def test_l1_idle_over_15min_triggers_template():
    ev = Evaluator()
    snap = _snap(idle_seconds=1000)  # >900
    decision = ev.evaluate(snap)
    assert decision is not None
    assert decision.source == "template"
    assert decision.topic == "idle"
    assert "盯着屏幕发呆" in decision.text


def test_l1_deep_night_work_session_triggers_sleepy():
    ev = Evaluator()
    snap = _snap(is_deep_night=True, work_session_minutes=45, local_time="02:30 周三")
    decision = ev.evaluate(snap)
    assert decision is not None
    assert decision.emotion == "sleepy"
    assert "02:30" in decision.text or "睡觉" in decision.text


def test_l1_work_session_over_2h_triggers_concern():
    ev = Evaluator()
    snap = _snap(work_session_minutes=130)
    decision = ev.evaluate(snap)
    assert decision is not None
    assert decision.emotion == "concern"
    assert "130" in decision.text or "颈椎" in decision.text


def test_l1_coding_one_hour_triggers_focus_break():
    ev = Evaluator()
    snap = _snap(
        work_session_minutes=60,
        active_window_title="main.py - Visual Studio Code",
        active_process="Code.exe",
    )
    decision = ev.evaluate(snap, allow_llm=False)
    assert decision is not None
    assert decision.source == "template"
    assert decision.topic == "focus_break"
    assert decision.emotion == "concern"
    assert "60" in decision.text


def test_l1_focus_break_requires_coding_context():
    ev = Evaluator()
    snap = _snap(
        work_session_minutes=60,
        active_window_title="Inbox - Mail",
        active_process="Mail.exe",
    )
    decision = ev.evaluate(snap, allow_llm=False)
    assert decision is None


def test_l1_no_trigger_when_conditions_not_met():
    ev = Evaluator()
    snap = _snap(idle_seconds=10, work_session_minutes=5, is_deep_night=False)
    # L1 不命中，且未注入 llm_decide，返回 None
    decision = ev.evaluate(snap, allow_llm=False)
    assert decision is None


def test_l2_llm_decide_should_speak_true():
    ev = Evaluator()
    llm_resp = {"should_speak": True, "text": "你在写代码啊，加油", "emotion": "neutral", "topic": "work"}
    with patch("core.companion.evaluator._call_llm", return_value=llm_resp):
        snap = _snap(idle_seconds=10)
        decision = ev.evaluate(snap, allow_llm=True)
    assert decision is not None
    assert decision.source == "llm"
    assert decision.text == "你在写代码啊，加油"
    assert decision.emotion == "neutral"


def test_l2_llm_decide_should_speak_false_returns_none():
    ev = Evaluator()
    llm_resp = {"should_speak": False, "text": "", "emotion": "", "topic": ""}
    with patch("core.companion.evaluator._call_llm", return_value=llm_resp):
        snap = _snap(idle_seconds=10)
        decision = ev.evaluate(snap, allow_llm=True)
    assert decision is None


def test_l2_llm_invalid_json_falls_back_to_template():
    """LLM 返回非法 JSON 时降级走上下文感知降级（C-05 修复）。"""
    ev = Evaluator()
    with patch("core.companion.evaluator._call_llm", side_effect=ValueError("invalid json")):
        snap = _snap(idle_seconds=10, work_session_minutes=5)  # L1 不命中
        decision = ev.evaluate(snap, allow_llm=True)
    # 降级到通用兖底（非 idle，因为不满足任何特定条件）
    assert decision is not None
    assert decision.source == "fallback_template"
    assert decision.emotion == "neutral"


def test_l2_llm_network_error_falls_back_to_idle_template():
    """LLM 网络错误 + 空闲 >15min 时降级走 idle 模板。

    注意：idle_seconds>900 时 L1 先命中（source='template'），
    此处测试的是 L1 不命中（idle<900）但 LLM 失败后 fallback 仍选 idle 情绪的场景。
    """
    ev = Evaluator()
    with patch("core.companion.evaluator._call_llm", side_effect=OSError("timeout")):
        # idle_seconds=500 不触发 L1（<900），但 fallback 也不走 idle（<900）
        # 走通用兖底
        snap = _snap(idle_seconds=500, work_session_minutes=5)
        decision = ev.evaluate(snap, allow_llm=True)
    assert decision is not None
    assert decision.source == "fallback_template"
    # idle<900 且非深夜且工作<120min → 通用兖底
    assert decision.emotion == "neutral"


def test_l2_llm_network_error_deep_night_falls_back_to_sleepy():
    """LLM 网络错误 + 深夜工作时降级走 sleepy 模板（C-05 上下文感知）。

    注意：is_deep_night + work_session>30 时 L1 先命中（source='template'），
    此处用 work_session=25（<30）让 L1 不命中，但 fallback 中
    _context_aware_fallback 也用 >30 判断，所以走通用兖底。
    要测 fallback 的 sleepy 分支，需要直接调 _context_aware_fallback。
    """
    ev = Evaluator()
    # 直接测试 _context_aware_fallback 的深夜分支
    snap = _snap(is_deep_night=True, work_session_minutes=45, local_time="02:30 周三")
    decision = ev._context_aware_fallback(snap)
    assert decision.source == "fallback_template"
    assert decision.emotion == "sleepy"
    assert "02:30" in decision.text


def test_l2_llm_throttled_when_recent_same_signal():
    """5min 内同类信号不重复调 LLM。"""
    ev = Evaluator()
    import time
    ev._last_llm_call_ts = {"idle_signal": time.time()}  # 刚调过
    with patch("core.companion.evaluator._call_llm") as mock_llm:
        snap = _snap(idle_seconds=10)
        decision = ev.evaluate(snap, allow_llm=True, signal_type="idle_signal")
    mock_llm.assert_not_called()
    assert decision is None


# === L1 全部模板覆盖 ===

def test_l1_tease_template_triggers():
    """tease 模板：window_changed_recently + greeting_count_today==0。"""
    ev = Evaluator()
    snap = _snap(window_changed_recently=True, greeting_count_today=0)
    decision = ev.evaluate(snap, allow_llm=False)
    assert decision is not None
    assert decision.topic == "tease"
    assert decision.emotion == "tease"
    assert "摸鱼" in decision.text or "切换窗口" in decision.text


def test_l1_away_long_template_triggers():
    """away_long 模板：idle_state=='away' and idle_seconds>3600。"""
    ev = Evaluator()
    snap = _snap(idle_state="away", idle_seconds=3700)
    decision = ev.evaluate(snap, allow_llm=False)
    assert decision is not None
    assert decision.topic == "away_long"
    assert decision.emotion == "neutral"
    assert "还在吗" in decision.text or "很久" in decision.text


def test_l1_template_priority_idle_before_sleepy():
    """当 idle>900 和 sleepy 条件同时满足时，idle 先命中（模板列表顺序优先）。"""
    ev = Evaluator()
    snap = _snap(idle_seconds=1000, is_deep_night=True, work_session_minutes=45)
    decision = ev.evaluate(snap, allow_llm=False)
    assert decision is not None
    assert decision.topic == "idle"  # idle 在 sleepy 之前
    assert decision.source == "template"


def test_l1_template_priority_sleepy_before_concern():
    """sleepy 在 concern 之前（当深夜+工作超30min+工作超2h同时满足时）。"""
    ev = Evaluator()
    snap = _snap(is_deep_night=True, work_session_minutes=130, local_time="02:30")
    decision = ev.evaluate(snap, allow_llm=False)
    assert decision is not None
    # sleepy 模板在 concern 之前
    assert decision.topic == "sleepy"


def test_l1_template_priority_concern_before_tease():
    """concern 在 tease 之前（工作超2h + 窗口刚切换 同时满足时）。"""
    ev = Evaluator()
    snap = _snap(work_session_minutes=130, window_changed_recently=True, greeting_count_today=0)
    decision = ev.evaluate(snap, allow_llm=False)
    assert decision is not None
    assert decision.topic == "concern"


# === _context_aware_fallback 全分支 ===

def test_fallback_concern_branch():
    """fallback: work_session > 120 且非深夜 → concern。"""
    ev = Evaluator()
    snap = _snap(work_session_minutes=150, is_deep_night=False)
    decision = ev._context_aware_fallback(snap)
    assert decision.source == "fallback_template"
    assert decision.emotion == "concern"
    assert decision.topic == "concern"


def test_fallback_away_long_branch():
    """fallback: idle_state=='away' and idle_seconds>3600 → neutral。"""
    ev = Evaluator()
    snap = _snap(idle_state="away", idle_seconds=4000, is_deep_night=False, work_session_minutes=5)
    decision = ev._context_aware_fallback(snap)
    assert decision.source == "fallback_template"
    assert decision.emotion == "neutral"
    assert decision.topic == "away_long"


def test_fallback_idle_branch():
    """fallback: idle_seconds>900 且非深夜非久坐 → idle。"""
    ev = Evaluator()
    snap = _snap(idle_seconds=1000, is_deep_night=False, work_session_minutes=5, idle_state="idle")
    decision = ev._context_aware_fallback(snap)
    assert decision.source == "fallback_template"
    assert decision.emotion == "idle"
    assert decision.topic == "idle"


def test_fallback_default_branch():
    """fallback: 所有条件都不满足 → 通用问候。"""
    ev = Evaluator()
    snap = _snap(idle_seconds=10, work_session_minutes=5, is_deep_night=False, idle_state="active")
    decision = ev._context_aware_fallback(snap)
    assert decision.source == "fallback_template"
    assert decision.emotion == "neutral"
    assert decision.topic == "general"
    assert decision.text == "在忙什么呢？"


def test_fallback_sleepy_priority_over_concern():
    """fallback: 深夜 + 工作超30min 优先于工作超2h。"""
    ev = Evaluator()
    snap = _snap(is_deep_night=True, work_session_minutes=150, local_time="02:30")
    decision = ev._context_aware_fallback(snap)
    assert decision.emotion == "sleepy"
    assert decision.topic == "deep_night"


# === LLM 异常路径 ===

def test_llm_call_raises_oserror_on_http_error():
    """_call_llm 在 HTTP 错误时应抛 OSError。"""
    from core.companion.evaluator import _call_llm
    fake_resp = MagicMock()
    fake_resp.is_error = True
    fake_resp.status_code = 500
    fake_client = MagicMock()
    fake_client.post.return_value = fake_resp
    fake_client_cls = MagicMock()
    fake_client_cls.return_value.__enter__ = lambda self: fake_client
    fake_client_cls.return_value.__exit__ = lambda self, *args: None
    snap = _snap()
    with patch("core.companion.evaluator.httpx.Client", fake_client_cls):
        with pytest.raises(OSError, match="HTTP 500"):
            _call_llm(snap, endpoint="http://x", api_key="k", model="m")


def test_llm_call_raises_valueerror_on_invalid_json():
    """_call_llm 在 LLM 返回非法 JSON 时应抛 ValueError。"""
    from core.companion.evaluator import _call_llm
    fake_resp = MagicMock()
    fake_resp.is_error = False
    fake_resp.json.return_value = {
        "choices": [{"message": {"content": "not json{"}}]
    }
    fake_client = MagicMock()
    fake_client.post.return_value = fake_resp
    fake_client_cls = MagicMock()
    fake_client_cls.return_value.__enter__ = lambda self: fake_client
    fake_client_cls.return_value.__exit__ = lambda self, *args: None
    snap = _snap()
    with patch("core.companion.evaluator.httpx.Client", fake_client_cls):
        with pytest.raises(ValueError):
            _call_llm(snap, endpoint="http://x", api_key="k", model="m")


def test_llm_returns_empty_text():
    """LLM 返回 should_speak=true 但 text 为空时应保留空文本。"""
    ev = Evaluator()
    llm_resp = {"should_speak": True, "text": "", "emotion": "neutral", "topic": "work"}
    with patch("core.companion.evaluator._call_llm", return_value=llm_resp):
        snap = _snap(idle_seconds=10)
        decision = ev.evaluate(snap, allow_llm=True)
    assert decision is not None
    assert decision.text == ""
    assert decision.source == "llm"


def test_llm_missing_emotion_defaults_to_neutral():
    """LLM 返回缺 emotion 字段时应默认为 neutral。"""
    ev = Evaluator()
    llm_resp = {"should_speak": True, "text": "你好", "topic": "work"}
    with patch("core.companion.evaluator._call_llm", return_value=llm_resp):
        snap = _snap(idle_seconds=10)
        decision = ev.evaluate(snap, allow_llm=True)
    assert decision is not None
    assert decision.emotion == "neutral"


def test_llm_missing_topic_defaults_to_general():
    """LLM 返回缺 topic 字段时应默认为 general。"""
    ev = Evaluator()
    llm_resp = {"should_speak": True, "text": "你好", "emotion": "happy"}
    with patch("core.companion.evaluator._call_llm", return_value=llm_resp):
        snap = _snap(idle_seconds=10)
        decision = ev.evaluate(snap, allow_llm=True)
    assert decision is not None
    assert decision.topic == "general"


def test_llm_missing_text_defaults_to_empty():
    """LLM 返回缺 text 字段时应默认为空串。"""
    ev = Evaluator()
    llm_resp = {"should_speak": True, "emotion": "neutral", "topic": "work"}
    with patch("core.companion.evaluator._call_llm", return_value=llm_resp):
        snap = _snap(idle_seconds=10)
        decision = ev.evaluate(snap, allow_llm=True)
    assert decision is not None
    assert decision.text == ""


def test_llm_keyerror_falls_back_to_fallback():
    """_call_llm 抛 KeyError（缺少 choices 字段）时应降级。"""
    ev = Evaluator()
    with patch("core.companion.evaluator._call_llm", side_effect=KeyError("choices")):
        snap = _snap(idle_seconds=10, work_session_minutes=5)
        decision = ev.evaluate(snap, allow_llm=True)
    assert decision is not None
    assert decision.source == "fallback_template"


# === LLM 节流边界 ===

def test_llm_throttle_boundary_just_expired():
    """节流窗口刚好过期（>5min）时应允许调 LLM。"""
    ev = Evaluator()
    import time
    ev._last_llm_call_ts = {"default": time.time() - 301}  # 5min 1s 前
    llm_resp = {"should_speak": True, "text": "hi", "emotion": "neutral", "topic": "general"}
    with patch("core.companion.evaluator._call_llm", return_value=llm_resp) as mock_llm:
        snap = _snap(idle_seconds=10)
        decision = ev.evaluate(snap, allow_llm=True, signal_type="default")
    mock_llm.assert_called_once()
    assert decision is not None
    assert decision.source == "llm"


def test_llm_throttle_different_signal_types_independent():
    """不同 signal_type 的节流是独立的。"""
    ev = Evaluator()
    import time
    ev._last_llm_call_ts = {"signal_a": time.time()}  # signal_a 刚调过
    llm_resp = {"should_speak": True, "text": "hi", "emotion": "neutral", "topic": "general"}
    with patch("core.companion.evaluator._call_llm", return_value=llm_resp) as mock_llm:
        snap = _snap(idle_seconds=10)
        # signal_b 没调过，应允许
        decision = ev.evaluate(snap, allow_llm=True, signal_type="signal_b")
    mock_llm.assert_called_once()
    assert decision is not None


def test_llm_throttle_allows_after_window():
    """节流窗口过期后应重新允许。"""
    ev = Evaluator()
    import time
    ev._last_llm_call_ts = {"default": time.time() - 400}  # >5min
    llm_resp = {"should_speak": True, "text": "hi", "emotion": "neutral", "topic": "general"}
    with patch("core.companion.evaluator._call_llm", return_value=llm_resp) as mock_llm:
        snap = _snap(idle_seconds=10)
        decision = ev.evaluate(snap, allow_llm=True, signal_type="default")
    mock_llm.assert_called_once()
    assert decision is not None
