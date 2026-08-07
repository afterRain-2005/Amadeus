"""OpenAI-compatible desktop tool-calling agent loop."""
from __future__ import annotations

from collections.abc import Callable
import json

import httpx

from core.desktop_tools import CONFIRMATION_REQUIRED, TOOL_DEFINITIONS, execute_tool


AGENT_RULES = """
You are also a Windows desktop agent. Use tools when the user asks you to inspect or operate the computer.
Before acting, inspect current state when needed. Never claim an operation succeeded without a tool result.
Prefer focused, reversible steps. Do not run destructive commands, change security settings, expose secrets,
purchase anything, or send messages/files without explicit confirmation. Keep tool status descriptions short.
After tools finish, answer using the character's required bilingual emotion format.
"""


def run_agent(
    *, endpoint: str, api_key: str, model: str, personality: str,
    history: list[dict], memories: list[dict], on_status: Callable[[str], None],
    confirm: Callable[[str, dict], bool], on_delta: Callable[[str], None],
) -> str:
    memory_text = "；".join(item.get("content", "") for item in memories[-8:])
    messages = [{"role": "system", "content": personality + AGENT_RULES + (f"\n【用户记忆】{memory_text}" if memory_text else "")}, *history[-14:]]
    url = endpoint.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    vision_capable = any(token in model.lower() for token in ("vision", "vl", "gpt-4o", "gpt-4.1", "mimo-v2.5"))

    for _ in range(10):
        content, tool_calls = _stream_turn(url, headers, model, messages, on_delta)
        if not tool_calls:
            return content.strip()

        message = {"role": "assistant", "content": content or None, "tool_calls": tool_calls}
        messages.append(message)
        for call in tool_calls:
            name = call["function"]["name"]
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            on_status(_status_text(name, arguments))
            allowed = name not in CONFIRMATION_REQUIRED or confirm(name, arguments)
            if not allowed:
                result = {"text": "User denied this operation."}
            elif name == "capture_screen" and not vision_capable:
                result = {"text": "The configured model cannot inspect images. Use list_windows or run_command to inspect desktop state."}
            else:
                try:
                    result = execute_tool(name, arguments)
                except Exception as exc:
                    result = {"text": f"Tool failed: {exc}"}
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result["text"]})
            if result.get("image_url") and vision_capable:
                messages.append({"role": "user", "content": [
                    {"type": "text", "text": "This is the screenshot returned by capture_screen. Analyze it for the current task."},
                    {"type": "image_url", "image_url": {"url": result["image_url"]}},
                ]})
    raise RuntimeError("Agent reached the maximum tool steps.")


def _stream_turn(url: str, headers: dict, model: str, messages: list[dict], on_delta) -> tuple[str, list[dict]]:
    content = ""
    calls: dict[int, dict] = {}
    with httpx.stream("POST", url, headers=headers, json={
        "model": model, "messages": messages, "tools": TOOL_DEFINITIONS,
        "tool_choice": "auto", "temperature": 0.7, "max_tokens": 700, "stream": True,
    }, timeout=90) as response:
        if response.is_error:
            error_body = response.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Model API HTTP {response.status_code}: {error_body[:800]}")
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                delta = json.loads(payload)["choices"][0]["delta"]
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            text = delta.get("content") or ""
            if text:
                content += text
                on_delta(text)
            for fragment in delta.get("tool_calls") or []:
                index = fragment.get("index", 0)
                call = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if fragment.get("id"):
                    call["id"] = fragment["id"]
                function = fragment.get("function") or {}
                call["function"]["name"] += function.get("name") or ""
                call["function"]["arguments"] += function.get("arguments") or ""
    return content, [calls[index] for index in sorted(calls)]


def _status_text(name: str, arguments: dict) -> str:
    labels = {"capture_screen": "正在观察屏幕…", "list_windows": "正在查看窗口…",
              "read_clipboard": "正在读取剪贴板…", "open_target": "准备打开目标…",
              "focus_window": "正在切换窗口…", "type_text": "准备输入文字…",
              "press_keys": "准备按下快捷键…", "click": "准备点击…", "run_command": "准备执行命令…"}
    return labels.get(name, f"正在执行 {name}…")
