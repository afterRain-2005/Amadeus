"""Hermes Agent backend client (run-based async approval flow) + 直连 fallback。

Hermes 模式：Hermes 在服务端跑自己的 agent loop 和工具系统，
amadeus-py 只负责：
1. POST /v1/runs 启动一个 run，拿 run_id
2. 流式监听 GET /v1/runs/{run_id}/events (SSE)
3. 把 message.delta 透传给 UI
4. 收到 approval.request 时弹窗让用户选择，POST /v1/runs/{run_id}/approval 回传 choice
5. run.completed 时返回 output

直连 fallback 模式（Hermes 不可用时自动回退）：
- 旧的 OpenAI 兼容 chat completions agent loop
- 本地工具执行（desktop_tools.py）
- 同步 confirm 回调（Yes/No）

参考接口规格：
- POST /v1/runs 请求体：{input, instructions?, session_id?, conversation_history?, previous_response_id?}
  响应：{run_id, status:"started"} HTTP 202。不传 model（服务端 profile 配置）
- GET /v1/runs/{run_id}/events：SSE 流
  事件类型：run.started / message.delta / tool.started / tool.completed /
           approval.request / approval.responded / run.completed / run.failed / run.cancelled
- approval.request payload：{event, run_id, timestamp, command, description,
                            pattern_key, pattern_keys, choices:["once","session","always","deny"]}
- POST /v1/runs/{run_id}/approval：{"choice": "once"|"session"|"always"|"deny", "resolve_all"?: bool}
- POST /v1/runs/{run_id}/stop：中断运行
- 可选会话头：X-Hermes-Session-Id（用于会话连续性）
"""
from __future__ import annotations

from collections.abc import Callable
import json
import sys

import httpx

from core.desktop_tools import CONFIRMATION_REQUIRED, TOOL_DEFINITIONS, execute_tool
from config import APPROVAL_POLICY


# approval.request 的合法 choice 值
APPROVAL_CHOICES = ("once", "session", "always", "deny")


# === 直连 fallback：旧 OpenAI 兼容 agent loop ===

_AGENT_RULES = """
You are also a Windows desktop agent. Use tools when the user asks you to inspect or operate the computer.
Before acting, inspect current state when needed. Never claim an operation succeeded without a tool result.
Prefer focused, reversible steps. Do not run destructive commands, change security settings, expose secrets,
purchase anything, or send messages/files without explicit confirmation. Keep tool status descriptions short.
After tools finish, answer using the character's required bilingual emotion format.
"""


def _stream_turn_direct(url: str, headers: dict, model: str, messages: list[dict], on_delta) -> tuple[str, list[dict]]:
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
    """工具执行进度文案，带图标 + 关键参数。"""
    if name == "web_search":
        return f"🔍 搜索：{arguments.get('query', '')}"
    if name == "fetch_url":
        return f"🌐 读取网页：{arguments.get('url', '')}"
    if name == "file_find":
        return f"🔎 查找文件：{arguments.get('pattern', '')}"
    if name == "list_dir":
        return f"📁 列目录：{arguments.get('path', '')}"
    if name == "read_file":
        return f"📄 读取：{arguments.get('path', '')}"
    if name == "write_file":
        return f"✏️ 写入：{arguments.get('path', '')}"
    if name == "operate_gui":
        return f"🖱 操作 GUI：{arguments.get('task', '')}"
    labels = {"capture_screen": "🖥 正在观察屏幕…", "list_windows": "🪟 正在查看窗口…",
              "read_clipboard": "📋 正在读取剪贴板…", "open_target": "📂 准备打开目标…",
              "focus_window": "🪟 正在切换窗口…", "type_text": "⌨️ 准备输入文字…",
              "press_keys": "⌨️ 准备按下快捷键…", "click": "🖱 准备点击…",
              "run_command": "⚙️ 准备执行命令…"}
    return labels.get(name, f"正在执行 {name}…")


def _load_soul_md(profile: str = "kurisu") -> str | None:
    """从 ~/.hermes/profiles/<profile>/SOUL.md 读取人设。"""
    from pathlib import Path
    path = Path.home() / ".hermes" / "profiles" / profile / "SOUL.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return None


# 模块级 approval 记忆（session 级在进程内，always 级持久化到 config）
_session_allowed: set[str] = set()


def run_local_run(
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
    profile: str = "kurisu",
) -> str:
    """本地 Hermes-like agent runner。

    与 run_hermes_run() 回调接口一致（on_delta/on_status/on_approval），
    但不走 HTTP，直接在进程内跑 OpenAI 兼容 chat completions agent loop + 本地工具执行。

    Approval 记忆：
    - once: 仅本次允许
    - session: 本次会话内允许（内存 set）
    - always: 永久允许（写入 config.json 的 always_approvals 列表）
    - deny: 拒绝
    """
    from core.storage import load_config, save_config

    # 构建 system prompt: SOUL.md（人设）+ instructions（输出格式）+ agent rules + memories
    system = soul_md + "\n\n" + instructions + _AGENT_RULES
    if memories:
        memory_text = "；".join(item.get("content", "") for item in memories[-8:])
        if memory_text:
            system += f"\n【用户记忆】{memory_text}"

    messages: list[dict] = [{"role": "system", "content": system}]
    if conversation_history:
        messages.extend(conversation_history[-14:])
    messages.append({"role": "user", "content": input_text})

    url = endpoint.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    vision_capable = any(t in model.lower() for t in ("vision", "vl", "gpt-4o", "gpt-4.1", "mimo-v2.5"))

    # 加载 always-approved 集合 + 审批策略
    config = load_config()
    always_allowed = set(config.get("always_approvals", []))
    policy = {**APPROVAL_POLICY, **config.get("approval_policy", {})}
    auto_allow_tools = set(policy.get("auto_allow_tools", []))
    auto_allow_commands = policy.get("auto_allow_commands", [])

    for _ in range(10):
        content, tool_calls = _stream_turn_direct(url, headers, model, messages, on_delta)
        if not tool_calls:
            return content.strip()

        messages.append({"role": "assistant", "content": content or None, "tool_calls": tool_calls})

        for call in tool_calls:
            name = call["function"]["name"]
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}

            pattern_key = name

            # --- 审批策略检查（三档：自动放行 / 已记忆 / 需确认） ---
            # 1. 策略白名单工具（只读/低风险）→ 自动放行
            if name in auto_allow_tools:
                pre_approved = True
            # 2. run_command 的安全命令前缀 → 自动放行
            elif name == "run_command":
                cmd_text = arguments.get("command", "").strip()
                pre_approved = (
                    any(cmd_text.startswith(prefix) for prefix in auto_allow_commands)
                    or pattern_key in _session_allowed
                    or pattern_key in always_allowed
                )
            # 3. 其他需确认工具 → 查 session/always 记忆
            else:
                pre_approved = pattern_key in _session_allowed or pattern_key in always_allowed

            if name in CONFIRMATION_REQUIRED and not pre_approved:
                # 构造 approval.request payload（与 Hermes 格式一致）
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
                except Exception as exc:
                    print(f"[local-agent] approval 回调异常: {exc}", file=sys.stderr)
                    choice = "deny"

                if choice not in APPROVAL_CHOICES:
                    choice = "deny"

                if choice == "deny":
                    result = {"text": "User denied this operation."}
                else:
                    # 记录 approval
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
                messages.append({"role": "user", "content": [
                    {"type": "text", "text": "Screenshot from capture_screen. Analyze it for the current task."},
                    {"type": "image_url", "image_url": {"url": result["image_url"]}},
                ]})

    raise RuntimeError("Agent 达到最大工具调用轮数限制（10 轮）")


def _execute_tool_safe(name: str, arguments: dict, vision_capable: bool) -> dict:
    """执行工具，捕获异常。对不支持视觉的模型跳过截图。"""
    if name == "capture_screen" and not vision_capable:
        return {"text": "The configured model cannot inspect images. Use list_windows or run_command instead."}
    try:
        return execute_tool(name, arguments)
    except Exception as exc:
        return {"text": f"Tool failed: {exc}"}


def run_hermes_run(
    *,
    base_url: str,
    api_key: str,
    input_text: str,
    instructions: str | None = None,
    conversation_history: list[dict] | None = None,
    session_id: str | None = None,
    on_delta: Callable[[str], None] = lambda _: None,
    on_status: Callable[[str], None] = lambda _: None,
    on_approval: Callable[[dict], str] = lambda _: "deny",
    start_timeout: float = 30.0,
    stream_timeout: float = 300.0,
) -> str:
    """启动一个 Hermes run 并流式消费事件直到 run.completed。

    Returns:
        run.completed 事件的 output 字段（模型最终回复文本）。

    Raises:
        RuntimeError: HTTP 失败、SSE 中断、run.failed、run.cancelled。

    on_approval 回调接收 approval.request 的完整 payload dict，必须返回
    "once"/"session"/"always"/"deny" 之一。回调异常会被捕获并默认 "deny"，
    以避免 UI 异常导致 SSE 消费线程卡死。
    """
    base = base_url.rstrip("/")
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if session_id:
        headers["X-Hermes-Session-Id"] = session_id

    body: dict = {"input": input_text}
    if instructions:
        body["instructions"] = instructions
    if conversation_history:
        body["conversation_history"] = conversation_history
    if session_id:
        body["session_id"] = session_id

    # 1. 启动 run → 拿 run_id
    with httpx.Client(timeout=start_timeout) as client:
        resp = client.post(f"{base}/v1/runs", headers=headers, json=body)
        if resp.status_code not in (200, 202):
            raise RuntimeError(
                f"Hermes /v1/runs HTTP {resp.status_code}: {resp.text[:800]}"
            )
        run_id = resp.json().get("run_id")
        if not run_id:
            raise RuntimeError(
                f"Hermes /v1/runs 返回无 run_id: {resp.text[:800]}"
            )

    # 2. 流式消费 SSE 事件
    events_url = f"{base}/v1/runs/{run_id}/events"
    return _consume_sse(
        events_url, headers, run_id, base,
        on_delta=on_delta, on_status=on_status, on_approval=on_approval,
        timeout=stream_timeout,
    )


def stop_hermes_run(base_url: str, api_key: str, run_id: str) -> None:
    """请求中断一个运行中的 Hermes run（best-effort，不抛异常）。"""
    base = base_url.rstrip("/")
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        with httpx.Client(timeout=10.0) as client:
            client.post(f"{base}/v1/runs/{run_id}/stop", headers=headers)
    except httpx.HTTPError as exc:
        print(f"[hermes] stop POST failed: {exc}", file=sys.stderr)


def _consume_sse(
    url: str,
    headers: dict,
    run_id: str,
    base_url: str,
    *,
    on_delta: Callable[[str], None],
    on_status: Callable[[str], None],
    on_approval: Callable[[dict], str],
    timeout: float,
) -> str:
    """逐行解析 SSE 流，遇到 run.completed 返回 output，遇到 run.failed/cancelled 抛异常。

    SSE 帧格式（标准 Server-Sent Events）：
        event: <event_name>
        data: <json_payload>

    帧之间用空行分隔。多个 data: 行按 SSE 规范用 \\n 拼接。
    run.completed 通过 _RunCompleted 异常打断循环并返回 output。
    """
    event_type = ""
    data_lines: list[str] = []

    try:
        with httpx.stream("GET", url, headers=headers, timeout=timeout) as response:
            if response.is_error:
                body_text = response.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"Hermes events HTTP {response.status_code}: {body_text[:800]}"
                )

            for raw_line in response.iter_lines():
                line = raw_line.rstrip("\r\n")
                if line == "":
                    # 空行 = 事件边界 → 派发已累积的事件
                    if data_lines:
                        _dispatch_event(
                            event_type, data_lines, run_id, base_url, headers,
                            on_delta=on_delta, on_status=on_status, on_approval=on_approval,
                        )
                    event_type = ""
                    data_lines = []
                    continue
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    # SSE 规范：data: 后若有一个前导空格则移除它（仅一个）
                    data = line[5:]
                    if data.startswith(" "):
                        data = data[1:]
                    data_lines.append(data)
                # 忽略注释（以 : 开头的行）和其他字段（id:/retry: 等）
    except _RunCompleted as done:
        return done.output

    raise RuntimeError("Hermes SSE 流结束但未收到 run.completed 事件")


def _dispatch_event(
    event_type: str,
    data_lines: list[str],
    run_id: str,
    base_url: str,
    headers: dict,
    *,
    on_delta: Callable[[str], None],
    on_status: Callable[[str], None],
    on_approval: Callable[[dict], str],
) -> None:
    """解析单个 SSE 事件并触发对应回调。

    遇到 run.completed 时 raise _RunCompleted（携带 output），由 _consume_sse 捕获返回。
    遇到 run.failed/cancelled 时 raise RuntimeError。
    """
    payload_text = "\n".join(data_lines)
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        # 非 JSON payload（如心跳/注释），忽略
        return

    name = event_type or payload.get("event", "")

    if name == "message.delta":
        delta = payload.get("delta", "")
        if delta:
            on_delta(delta)
    elif name == "tool.started":
        tool_name = payload.get("tool") or payload.get("name") or "tool"
        on_status(f"正在执行 {tool_name}…")
    elif name == "tool.completed":
        tool_name = payload.get("tool") or payload.get("name") or "tool"
        on_status(f"{tool_name} 完成")
    elif name == "approval.request":
        _handle_approval(payload, run_id, base_url, headers, on_approval)
    elif name == "run.completed":
        # 用异常打断 SSE 循环并返回 output（见 _consume_sse 的 try/except）
        raise _RunCompleted(payload.get("output", ""))
    elif name == "run.failed":
        raise RuntimeError(f"Hermes run 失败: {payload.get('error', payload)}")
    elif name == "run.cancelled":
        raise RuntimeError("Hermes run 被取消")
    # run.started / approval.responded 等事件无需处理


class _RunCompleted(Exception):
    """内部信号异常：SSE 收到 run.completed，携带 output 文本。"""

    def __init__(self, output: str) -> None:
        super().__init__(output)
        self.output = output


def _handle_approval(
    payload: dict,
    run_id: str,
    base_url: str,
    headers: dict,
    on_approval: Callable[[dict], str],
) -> None:
    """处理 approval.request 事件：弹窗回调 → POST choice 回服务器。

    回调异常会被捕获并默认 "deny"，避免 UI 崩溃导致 SSE 消费线程卡死。
    """
    try:
        choice = on_approval(payload)
    except Exception as exc:
        print(f"[hermes] approval 回调异常: {exc}", file=sys.stderr)
        choice = "deny"
    if choice not in APPROVAL_CHOICES:
        choice = "deny"
    _post_approval(base_url, headers, run_id, choice)


def _post_approval(base_url: str, headers: dict, run_id: str, choice: str) -> None:
    """POST /v1/runs/{run_id}/approval，best-effort，失败仅 stderr 告警。"""
    url = f"{base_url.rstrip('/')}/v1/runs/{run_id}/approval"
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers=headers, json={"choice": choice})
            if resp.is_error:
                print(
                    f"[hermes] approval POST HTTP {resp.status_code}: {resp.text[:300]}",
                    file=sys.stderr,
                )
    except httpx.HTTPError as exc:
        print(f"[hermes] approval POST 失败: {exc}", file=sys.stderr)
