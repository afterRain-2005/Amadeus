"""Evaluator 测试：L1 硬阈值规则 + L2 LLM 决策 + LLM 失败降级。"""
import json
from unittest.mock import patch, MagicMock

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
    """LLM 返回非法 JSON 时降级走 L1 模板（即便本场景非必说）。"""
    ev = Evaluator()
    with patch("core.companion.evaluator._call_llm", side_effect=ValueError("invalid json")):
        snap = _snap(idle_seconds=10, work_session_minutes=5)  # L1 不命中
        decision = ev.evaluate(snap, allow_llm=True)
    # 降级到 idle 模板兜底
    assert decision is not None
    assert decision.source == "fallback_template"
    assert decision.emotion == "idle"


def test_l2_llm_network_error_falls_back_to_template():
    ev = Evaluator()
    with patch("core.companion.evaluator._call_llm", side_effect=OSError("timeout")):
        snap = _snap(idle_seconds=10)
        decision = ev.evaluate(snap, allow_llm=True)
    assert decision is not None
    assert decision.source == "fallback_template"


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
