"""Persistent terminal preferences and shell-like command edge cases."""
from __future__ import annotations

def test_terminal_state_round_trip_is_utf8_and_bounded(tmp_path, monkeypatch):
    import core.terminal_state as state

    monkeypatch.setattr(state, "APP_DIR", tmp_path)
    monkeypatch.setattr(state, "_STATE_PATH", tmp_path / "terminal_state.json")
    history = [f"命令 {index}" for index in range(250)]

    state.save_terminal_state(
        history=history,
        route="harness",
        cwd=tmp_path,
        session_id="amadeus-terminal-test",
    )
    loaded = state.load_terminal_state()

    assert loaded["route"] == "harness"
    assert loaded["cwd"] == str(tmp_path)
    assert loaded["session_id"] == "amadeus-terminal-test"
    assert loaded["history"] == history[-200:]
    assert "命令 249" in (tmp_path / "terminal_state.json").read_text(encoding="utf-8")


def test_terminal_state_corrupt_file_falls_back(tmp_path, monkeypatch):
    import core.terminal_state as state

    path = tmp_path / "terminal_state.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(state, "_STATE_PATH", path)

    loaded = state.load_terminal_state()

    assert loaded["history"] == []
    assert loaded["route"] == "auto"
    assert loaded["cwd"] == ""
    assert loaded["session_id"].startswith("amadeus-terminal-")


def test_terminal_history_clear_and_cd_expands_user_path(tmp_path, monkeypatch):
    from core.terminal_commands import TerminalCommandContext, registry

    monkeypatch.setenv("AMADEUS_TERMINAL_ROOT", str(tmp_path))
    context = TerminalCommandContext(
        route_mode="auto",
        cwd=tmp_path,
        history=["one", "two"],
        active_skills=[],
        list_skills=lambda: [],
        enable_skill=lambda name: (False, name),
        clear_skills=lambda: None,
        new_session=lambda: None,
    )

    cleared = registry.dispatch("/history clear", context)
    changed = registry.dispatch("/cd $AMADEUS_TERMINAL_ROOT", context)

    assert context.history == []
    assert "history cleared" in cleared.lines[0][1]
    assert changed.cwd == tmp_path.resolve()
