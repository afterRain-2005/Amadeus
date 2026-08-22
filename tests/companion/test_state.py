from core.companion.sensors import ContextSnapshot
from core.companion.state import PresenceStateEngine


def _snap(**kwargs) -> ContextSnapshot:
    defaults = dict(
        timestamp="2026-08-16T10:00:00Z",
        local_time="14:30",
        is_deep_night=False,
        idle_seconds=10,
        work_session_minutes=5,
        idle_state="active",
        active_window_title="main.py - Code",
        active_process="Code.exe",
        window_changed_recently=False,
        last_companion_greeting_ts=None,
        last_companion_topic=None,
        greeting_count_today=0,
    )
    defaults.update(kwargs)
    return ContextSnapshot(**defaults)


def test_presence_detects_focused_session():
    engine = PresenceStateEngine({"focus_minutes": 25, "deep_focus_minutes": 45})
    state = engine.update_from_snapshot(
        _snap(work_session_minutes=30), local_hour=14
    )
    assert state.focus_state == "focused"
    assert state.attention == "focused"
    assert state.mood == "neutral"


def test_presence_detects_deep_focus_mood():
    engine = PresenceStateEngine({"focus_minutes": 25, "deep_focus_minutes": 45})
    state = engine.update_from_snapshot(
        _snap(work_session_minutes=50), local_hour=14
    )
    assert state.focus_state == "deep_focus"
    assert state.mood == "focused"


def test_presence_idle_overrides_focus():
    engine = PresenceStateEngine({"focus_minutes": 25, "deep_focus_minutes": 45})
    state = engine.update_from_snapshot(
        _snap(idle_seconds=600, idle_state="idle", work_session_minutes=60),
        local_hour=14,
    )
    assert state.focus_state == "stale"
    assert state.attention == "idle"


def test_presence_deep_night_lowers_energy_and_mood():
    engine = PresenceStateEngine()
    state = engine.update_from_snapshot(
        _snap(is_deep_night=True, work_session_minutes=40), local_hour=2
    )
    assert state.mood == "tired"
    assert state.energy < 70


def test_presence_call_and_user_events():
    engine = PresenceStateEngine()
    engine.on_user_message()
    assert engine.state.attention == "engaged"
    engine.on_call_started()
    assert engine.state.attention == "in_call"
    engine.on_call_ended()
    assert engine.state.attention == "watching"
