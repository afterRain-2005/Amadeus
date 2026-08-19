"""DeepSeek harness-style agent turn runner.

This adapter keeps DeepSeek reasoning content in the internal message loop so
tool-calling turns can be replayed without losing the reasoning trace.
"""
from __future__ import annotations

from collections.abc import Callable
import json

import httpx

from core.desktop_tools import (
    CONFIRMATION_REQUIRED,
    TOOL_DEFINITIONS,
    execute_tool,
    is_auto_approved_command,
)
from config import APPROVAL_POLICY

APPROVAL_CHOICES = ("once", "session", "always", "deny")
_session_allowed: set[str] = set()


def _status_text(name: str, arguments: dict) -> str:
    if name == "web_search":
        return f"搜索 {arguments.get('query', '')}"
    if name == "fetch_url":
        return f"打开 {arguments.get('url', '')}"
    if name == "file_find":
        return f"查找 {arguments.get('pattern', '')}"
    if name == "list_dir":
        return f"浏览 {arguments.get('path', '')}"
    if name == "read_file":
        return f"读取 {arguments.get('path', '')}"
    if name == "write_file":
        return f"写入 {arguments.get('path', '')}"
    if name == "operate_gui":
        return f"操作 GUI {arguments.get('task', '')}"
    return name


def _stream_turn(
    url: str,
    headers: dict,
    model: str,
    messages: list[dict],
    on_delta,
) -> tuple[str, str, list[dict]]:
    content = ""
    reasoning = ""
    calls: dict[int, dict] = {}
    with httpx.stream(
        "POST",
        url,
        headers=headers,
        json={
            "model": model,
            "messages": messages,
            "tools": TOOL_DEFINITIONS,
            "tool_choice": "auto",
            "temperature": 0.2,
            "max_tokens": 1024,
            "stream": True,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        },
        timeout=90,
    ) as response:
        if response.is_error:
            error_body = response.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek API HTTP {response.status_code}: {error_body[:800]}")
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                delta = json.loads(payload)["choices"][0]["delta"]
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                continue
            text = delta.get("content") or ""
            if text:
                content += text
                on_delta(text)
            thought = delta.get("reasoning_content") or ""
            if thought:
                reasoning += thought
            for fragment in delta.get("tool_calls") or []:
                index = fragment.get("index", 0)
                call = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if fragment.get("id"):
                    call["id"] = fragment["id"]
                function = fragment.get("function") or {}
                call["function"]["name"] += function.get("name") or ""
                call["function"]["arguments"] += function.get("arguments") or ""
    return content, reasoning, [calls[index] for index in sorted(calls)]


def run_deepseek_turn(
    *,
    endpoint: str,
    api_key: str,
    model: str,
    soul_md: str,
    instructions: str,
    input_text: str,
    conversation_history: list[dict] | None = None,
    memories: list[dict] | None = None,
    on_delta: Callable[[str], None] = lambda _: None,
    on_status: Callable[[str], None] = lambda _: None,
    on_approval: Callable[[dict], str] = lambda _: "deny",
) -> str:
    from core.storage import load_config, save_config

    system = soul_md + "\n\n" + instructions
    if memories:
        memory_text = "\n".join(item.get("content", "") for item in memories[-8:])
        if memory_text:
            system += f"\n\nMemory:\n{memory_text}"

    messages: list[dict] = [{"role": "system", "content": system}]
    if conversation_history:
        for item in conversation_history[-14:]:
            msg = {k: v for k, v in item.items() if k in {"role", "content", "tool_calls", "tool_call_id", "name", "reasoning_content"}}
            if msg.get("role") == "assistant" and "reasoning_content" not in msg:
                msg.pop("reasoning_content", None)
            messages.append(msg)
    messages.append({"role": "user", "content": input_text})

    url = endpoint.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    config = load_config()
    always_allowed = set(config.get("always_approvals", []))
    policy = {**APPROVAL_POLICY, **config.get("approval_policy", {})}
    auto_allow_tools = set(policy.get("auto_allow_tools", []))
    auto_allow_commands = policy.get("auto_allow_commands", [])
    vision_capable = any(t in model.lower() for t in ("vision", "vl", "gpt-4o", "gpt-4.1", "mimo-v2.5"))
    last_reasoning = ""

    for _ in range(10):
        content, reasoning, tool_calls = _stream_turn(url, headers, model, messages, on_delta)
        last_reasoning = reasoning or last_reasoning
        if not tool_calls:
            return content.strip()

        assistant_msg = {"role": "assistant", "content": content or None, "tool_calls": tool_calls}
        if reasoning:
            assistant_msg["reasoning_content"] = reasoning
        messages.append(assistant_msg)

        for call in tool_calls:
            name = call["function"]["name"]
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}

            pattern_key = name
            if name in auto_allow_tools:
                pre_approved = True
            elif name == "run_command":
                cmd_text = arguments.get("command", "").strip()
                pre_approved = (
                    is_auto_approved_command(cmd_text, auto_allow_commands)
                    or pattern_key in _session_allowed
                    or pattern_key in always_allowed
                )
            else:
                pre_approved = pattern_key in _session_allowed or pattern_key in always_allowed

            if name in CONFIRMATION_REQUIRED and not pre_approved:
                payload = {
                    "event": "approval.request",
                    "run_id": "local",
                    "timestamp": __import__("time").time(),
                    "command": name,
                    "description": f"{name}: {json.dumps(arguments, ensure_ascii=False)}",
                    "pattern_key": pattern_key,
                    "pattern_keys": [pattern_key],
                    "choices": list(APPROVAL_CHOICES),
                }
                try:
                    choice = on_approval(payload)
                except Exception:
                    choice = "deny"
                if choice not in APPROVAL_CHOICES:
                    choice = "deny"
                if choice == "deny":
                    result = {"text": "User denied this operation."}
                else:
                    if choice == "session":
                        _session_allowed.add(pattern_key)
                    elif choice == "always":
                        always_allowed.add(pattern_key)
                        config["always_approvals"] = list(always_allowed)
                        save_config(config)
                    on_status(_status_text(name, arguments))
                    result = _execute_tool_safe(name, arguments, vision_capable)
            else:
                on_status(_status_text(name, arguments))
                result = _execute_tool_safe(name, arguments, vision_capable)

            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result["text"]})
            if result.get("image_url") and vision_capable:
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Screenshot from capture_screen. Analyze it for the current task."},
                        {"type": "image_url", "image_url": {"url": result["image_url"]}},
                    ],
                })

    raise RuntimeError("DeepSeek harness exhausted turn budget without a final reply")


def _execute_tool_safe(name: str, arguments: dict, vision_capable: bool) -> dict:
    if name == "capture_screen" and not vision_capable:
        return {"text": "The configured model cannot inspect images. Use list_windows or run_command instead."}
    try:
        return execute_tool(name, arguments)
    except Exception as exc:
        return {"text": f"Tool failed: {exc}"}
