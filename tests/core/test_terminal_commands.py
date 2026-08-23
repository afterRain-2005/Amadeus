"""终端 slash 命令注册表测试：/help /clear /new /status /route /pwd /cd /history /skills /skill 与 ! 转发。"""
from __future__ import annotations

from pathlib import Path

from core.terminal_commands import TerminalCommandContext, registry


def _ctx(tmp_path: Path, **overrides) -> TerminalCommandContext:
    """构造测试用 TerminalCommandContext，默认值覆盖终端命令的常见分支。"""
    defaults = dict(
        route_mode="auto",
        cwd=tmp_path,
        history=[],
        active_skills=[],
        list_skills=lambda: [],
        enable_skill=lambda name: (False, f"skill not found: {name}"),
        clear_skills=lambda: None,
        new_session=lambda: None,
    )
    defaults.update(overrides)
    return TerminalCommandContext(**defaults)


def test_non_slash_text_is_not_handled(tmp_path):
    result = registry.dispatch("hello world", _ctx(tmp_path))
    assert result.handled is False


def test_help_lists_commands(tmp_path):
    result = registry.dispatch("/help", _ctx(tmp_path))
    assert result.handled is True
    assert any(line[0] == "sys" and "/help" in line[1] for line in result.lines)


def test_clear_sets_clear_flag(tmp_path):
    result = registry.dispatch("/clear", _ctx(tmp_path))
    assert result.clear is True


def test_new_calls_new_session_and_clears(tmp_path):
    called = {"n": 0}

    def new_session():
        called["n"] += 1

    result = registry.dispatch("/new", _ctx(tmp_path, new_session=new_session))
    assert called["n"] == 1
    assert result.clear is True


def test_status_shows_route_cwd_skills(tmp_path):
    result = registry.dispatch("/status", _ctx(tmp_path, active_skills=["greet"]))
    text = result.lines[0][1]
    assert "route=auto" in text
    assert str(tmp_path) in text
    assert "skills=greet" in text


def test_route_without_args_shows_current(tmp_path):
    result = registry.dispatch("/route", _ctx(tmp_path, route_mode="harness"))
    assert "route=harness" in result.lines[0][1]


def test_route_sets_valid_mode(tmp_path):
    result = registry.dispatch("/route local", _ctx(tmp_path))
    assert result.route_mode == "local"


def test_route_rejects_invalid_mode(tmp_path):
    result = registry.dispatch("/route bogus", _ctx(tmp_path))
    assert result.lines[0][0] == "err"


def test_pwd_prints_cwd(tmp_path):
    result = registry.dispatch("/pwd", _ctx(tmp_path))
    assert result.lines[0][1] == str(tmp_path)


def test_cd_changes_to_existing_directory(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    result = registry.dispatch(f"/cd {sub}", _ctx(tmp_path))
    assert result.cwd == sub.resolve()


def test_cd_rejects_missing_directory(tmp_path):
    result = registry.dispatch("/cd nope", _ctx(tmp_path))
    assert result.lines[0][0] == "err"


def test_history_shows_recent_entries(tmp_path):
    result = registry.dispatch("/history", _ctx(tmp_path, history=["/pwd", "/status"]))
    assert "/pwd" in result.lines[0][1]
    assert "/status" in result.lines[0][1]


def test_history_empty(tmp_path):
    result = registry.dispatch("/history", _ctx(tmp_path))
    assert "history empty" in result.lines[0][1]


def test_skills_lists_discovered(tmp_path):
    def list_skills():
        return [("greet", "say hello", "project")]

    result = registry.dispatch("/skills", _ctx(tmp_path, list_skills=list_skills))
    assert "greet" in result.lines[0][1]
    assert "say hello" in result.lines[0][1]


def test_skill_enable(tmp_path):
    def enable_skill(name):
        return True, f"skill enabled: {name}"

    result = registry.dispatch("/skill greet", _ctx(tmp_path, enable_skill=enable_skill))
    assert result.lines[0][0] == "sys"
    assert "greet" in result.lines[0][1]


def test_skill_off_clears(tmp_path):
    called = {"n": 0}

    def clear_skills():
        called["n"] += 1

    result = registry.dispatch("/skill off", _ctx(tmp_path, clear_skills=clear_skills))
    assert called["n"] == 1


def test_skill_unknown_reports_error(tmp_path):
    result = registry.dispatch("/skill missing", _ctx(tmp_path))
    assert result.lines[0][0] == "err"


def test_unknown_command_reports_error(tmp_path):
    result = registry.dispatch("/bogus", _ctx(tmp_path))
    assert result.lines[0][0] == "err"
    assert "unknown command" in result.lines[0][1]


def test_bang_forwards_shell_command(tmp_path):
    result = registry.dispatch("!dir", _ctx(tmp_path))
    assert result.forward_text is not None
    assert "command: dir" in result.forward_text
    assert str(tmp_path) in result.forward_text
