"""Slash command registry for the CRT terminal."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shlex
from typing import Callable


Line = tuple[str, str] | tuple[str, str, dict]


@dataclass
class TerminalCommandContext:
    route_mode: str
    cwd: Path
    history: list[str]
    active_skills: list[str]
    list_skills: Callable[[], list[tuple[str, str, str]]]
    enable_skill: Callable[[str], tuple[bool, str]]
    clear_skills: Callable[[], None]
    new_session: Callable[[], None]


@dataclass
class TerminalCommandResult:
    handled: bool = True
    lines: list[Line] = field(default_factory=list)
    clear: bool = False
    route_mode: str | None = None
    cwd: Path | None = None
    forward_text: str | None = None


CommandHandler = Callable[[list[str], TerminalCommandContext], TerminalCommandResult]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    usage: str
    description: str
    handler: CommandHandler


class TerminalCommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}

    def register(self, name: str, usage: str, description: str):
        def decorator(handler: CommandHandler):
            self._commands[name] = CommandSpec(name, usage, description, handler)
            return handler
        return decorator

    def dispatch(self, text: str, context: TerminalCommandContext) -> TerminalCommandResult:
        stripped = text.strip()
        if not stripped.startswith("/") and not stripped.startswith("!"):
            return TerminalCommandResult(handled=False)
        if stripped.startswith("!"):
            command = stripped[1:].strip()
            if not command:
                return TerminalCommandResult(lines=[("err", "usage: !<command>")])
            return TerminalCommandResult(
                forward_text=(
                    "Run this shell command in the current terminal working directory, "
                    "using the normal tool approval flow:\n"
                    f"cwd: {context.cwd}\ncommand: {command}"
                )
            )
        try:
            parts = shlex.split(stripped, posix=False)
        except ValueError as exc:
            return TerminalCommandResult(lines=[("err", f"command parse error: {exc}")])
        if not parts:
            return TerminalCommandResult(handled=False)
        name = parts[0][1:]
        spec = self._commands.get(name)
        if spec is None:
            return TerminalCommandResult(lines=[("err", f"unknown command: /{name}. Try /help")])
        return spec.handler(parts[1:], context)

    def help_lines(self) -> list[str]:
        return [f"/{spec.usage} - {spec.description}" for spec in sorted(self._commands.values(), key=lambda s: s.name)]

    def slash_completions(self) -> list[tuple[str, str]]:
        """返回 [(命令名, 描述)]，供终端输入框 `/` 触发下拉补全面板。"""
        return [
            (spec.name, spec.description)
            for spec in sorted(self._commands.values(), key=lambda s: s.name)
        ]


registry = TerminalCommandRegistry()


@registry.register("help", "help", "show terminal commands")
def _help(_args: list[str], _ctx: TerminalCommandContext) -> TerminalCommandResult:
    return TerminalCommandResult(lines=[("sys", "\n".join(registry.help_lines()))])


@registry.register("clear", "clear", "clear terminal output")
def _clear(_args: list[str], _ctx: TerminalCommandContext) -> TerminalCommandResult:
    return TerminalCommandResult(clear=True)


@registry.register("new", "new", "start a new conversation session")
def _new(_args: list[str], ctx: TerminalCommandContext) -> TerminalCommandResult:
    ctx.new_session()
    return TerminalCommandResult(clear=True, lines=[("sys", "new session created")])


@registry.register("status", "status", "show route, cwd and enabled skills")
def _status(_args: list[str], ctx: TerminalCommandContext) -> TerminalCommandResult:
    skills = ", ".join(ctx.active_skills) if ctx.active_skills else "(none)"
    return TerminalCommandResult(lines=[("sys", f"route={ctx.route_mode}\ncwd={ctx.cwd}\nskills={skills}")])


@registry.register("route", "route [auto|local|harness]", "show or change terminal routing")
def _route(args: list[str], ctx: TerminalCommandContext) -> TerminalCommandResult:
    if not args:
        return TerminalCommandResult(lines=[("sys", f"route={ctx.route_mode}")])
    mode = args[0].lower()
    if mode not in {"auto", "local", "harness"}:
        return TerminalCommandResult(lines=[("err", "usage: /route [auto|local|harness]")])
    return TerminalCommandResult(route_mode=mode, lines=[("sys", f"route set to {mode}")])


@registry.register("pwd", "pwd", "print terminal working directory")
def _pwd(_args: list[str], ctx: TerminalCommandContext) -> TerminalCommandResult:
    return TerminalCommandResult(lines=[("sys", str(ctx.cwd))])


@registry.register("cd", "cd <path>", "change terminal working directory")
def _cd(args: list[str], ctx: TerminalCommandContext) -> TerminalCommandResult:
    if not args:
        return TerminalCommandResult(lines=[("sys", str(ctx.cwd))])
    target = Path(args[0].strip("\"'"))
    if not target.is_absolute():
        target = ctx.cwd / target
    try:
        resolved = target.resolve()
    except OSError as exc:
        return TerminalCommandResult(lines=[("err", f"cd failed: {exc}")])
    if not resolved.exists() or not resolved.is_dir():
        return TerminalCommandResult(lines=[("err", f"not a directory: {resolved}")])
    return TerminalCommandResult(cwd=resolved, lines=[("sys", f"cwd={resolved}")])


@registry.register("history", "history", "show terminal input history")
def _history(_args: list[str], ctx: TerminalCommandContext) -> TerminalCommandResult:
    items = ctx.history[-30:]
    if not items:
        return TerminalCommandResult(lines=[("sys", "(history empty)")])
    return TerminalCommandResult(lines=[("sys", "\n".join(f"{i + 1:>2}  {item}" for i, item in enumerate(items)))])


@registry.register("skills", "skills", "list discovered skills")
def _skills(_args: list[str], ctx: TerminalCommandContext) -> TerminalCommandResult:
    skills = ctx.list_skills()
    if not skills:
        return TerminalCommandResult(lines=[("sys", "no skills found")])
    text = "\n".join(f"{name} [{source}] {description}".rstrip() for name, description, source in skills)
    return TerminalCommandResult(lines=[("sys", text)])


@registry.register("skill", "skill <name|off>", "enable one skill or disable all skills")
def _skill(args: list[str], ctx: TerminalCommandContext) -> TerminalCommandResult:
    if not args:
        active = ", ".join(ctx.active_skills) if ctx.active_skills else "(none)"
        return TerminalCommandResult(lines=[("sys", f"active skills: {active}")])
    name = args[0]
    if name.lower() == "off":
        ctx.clear_skills()
        return TerminalCommandResult(lines=[("sys", "all skills disabled")])
    ok, message = ctx.enable_skill(name)
    return TerminalCommandResult(lines=[("sys" if ok else "err", message)])
