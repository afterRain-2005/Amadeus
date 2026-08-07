"""Codex CLI JSONL bridge used by the desktop pet UI."""
from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import subprocess


NODE = "C:\\Program Files\\nodejs\\node.exe"
CODEX = str(Path.home() / "AppData" / "Roaming" / "npm" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js")
DESKTOP_ROOT = str(Path(__file__).resolve().parents[3])
REQUEST_FILE = Path(__file__).resolve().parents[1] / "data" / "codex-request.txt"


def run_codex(
    prompt: str, session_id: str, on_status: Callable[[str], None],
    on_delta: Callable[[str], None],
) -> tuple[str, str]:
    REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    REQUEST_FILE.write_text(prompt, encoding="utf-8")
    wire_prompt = (
        "Read the UTF-8 user request from D:/Desktop/Ideas/Amadeus2026/amadeus-py/data/codex-request.txt, "
        "then fulfill that exact request. Do not discuss the request file itself."
    )
    global_options = ["-a", "on-request", "-s", "workspace-write", "-C", DESKTOP_ROOT]
    if session_id:
        command = [NODE, CODEX, *global_options, "exec", "resume", "--json", "--skip-git-repo-check", session_id, wire_prompt]
    else:
        command = [
            NODE, CODEX, *global_options, "exec", "--json", "--skip-git-repo-check", wire_prompt,
        ]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW,
    )
    thread_id = session_id
    answer = ""
    diagnostics = []
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line.startswith("{"):
            if line:
                diagnostics.append(line)
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        if event_type == "thread.started":
            thread_id = event.get("thread_id", thread_id)
        elif event_type == "turn.started":
            on_status("正在处理任务…")
        elif event_type == "item.started":
            on_status(_item_status(event.get("item", {})))
        elif event_type == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                text = item.get("text", "")
                if text:
                    answer = text
                    on_delta(text)
            else:
                on_status(_item_status(item))
    return_code = process.wait()
    if return_code != 0 and not answer:
        detail = " | ".join(diagnostics[-4:])
        raise RuntimeError(f"Codex CLI exited with code {return_code}: {detail}")
    return answer, thread_id


def _item_status(item: dict) -> str:
    item_type = item.get("type", "")
    if item_type == "command_execution":
        command = item.get("command", "")
        return "正在执行：" + (command[:34] + "…" if len(command) > 35 else command)
    if item_type in {"mcp_tool_call", "tool_call"}:
        return "正在调用桌面工具…"
    if item_type == "reasoning":
        return "正在分析…"
    return "正在处理任务…"
