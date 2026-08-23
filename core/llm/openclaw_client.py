"""OpenClaw Gateway 客户端：配置合并 / 探活 / 自动拉起 / 对话后端 / CUA 任务委托。

OpenClaw 是 Node.js 个人 AI 助理平台（npm install -g openclaw），Gateway 默认监听
127.0.0.1:18789，暴露 OpenAI 兼容 HTTP API（官方文档 https://docs.openclaw.ai/gateway）：
- GET  /v1/models            列出可用代理（openclaw / openclaw/default / openclaw/<agentId>）
- POST /v1/chat/completions  OpenAI 兼容对话（支持 stream:true SSE）
- 鉴权：Authorization: Bearer <OPENCLAW_GATEWAY_TOKEN>（onboard 时生成的 shared-secret）

amadeus-py 用两条路径接入：
1. 对话后端（agent_router.mode = "openclaw"）：整轮对话委托给 OpenClaw 默认代理，
   由其 agent loop / skills 处理，message.delta 流式回传 UI。
2. CUA 工具（desktop_tools.operate_gui）：GUI 操作任务委托给 OpenClaw 代理执行。

网关生命周期与 Hermes（hermes_launcher.ensure_gateway）同构：探活失败且 autostart
开启时 Popen("openclaw gateway --port <port>") 拉起分离进程，日志落 APP_DIR/openclaw_gateway.log；
桌宠退出不杀网关。probe/popen 参数为依赖注入，供测试 mock。
"""
from __future__ import annotations

from collections.abc import Callable
import json
import os
import subprocess
import time
from urllib.parse import urlparse

import httpx

from config import OPENCLAW_DEFAULTS
from core.storage import APP_DIR


def merge_config(config: dict | None = None) -> dict:
    """合并 OPENCLAW_DEFAULTS 与运行时 config["openclaw"]。

    不传 config 时自动 load_config()（desktop_tools 等无 config 上下文的调用方用）。
    backend_router 显式传入当前对话配置，避免二次读盘。
    """
    if config is None:
        from core.storage import load_config
        config = load_config()
    overrides = config.get("openclaw") if isinstance(config.get("openclaw"), dict) else {}
    return {**OPENCLAW_DEFAULTS, **overrides}


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def probe_gateway(base_url: str, token: str = "", timeout: float = 2.0) -> bool:
    """GET /v1/models 探活：HTTP 200 即网关在线（token 错会返回 401 → False）。"""
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{base_url.rstrip('/')}/v1/models", headers=_auth_headers(token))
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def ensure_gateway(
    *,
    base_url: str,
    token: str = "",
    autostart: bool = True,
    wait_timeout: float = 30.0,
    log_path: str | None = None,
    probe=None,
    popen=subprocess.Popen,
) -> bool:
    """探活 → 不通且 autostart 时拉起 `openclaw gateway` 子进程 → 轮询探活。

    返回最终是否可用；False 由调用方降级（对话后端回落本地直连，CUA 返回降级提示）。
    网关常驻，桌宠退出不杀（同 Hermes / GPT-SoVITS 惯例）。
    """
    probe = probe or probe_gateway
    if probe(base_url, token):
        return True
    if not autostart:
        return False
    log_file = APP_DIR / (log_path or "openclaw_gateway.log")
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    port = urlparse(base_url).port or 18789
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("ab") as fh:
            popen(
                ["openclaw", "gateway", "--port", str(port)],
                stdout=fh, stderr=fh, creationflags=flags,
                stdin=subprocess.DEVNULL,
            )
    except OSError:
        # openclaw 不在 PATH（未部署）或 Popen 失败
        return False
    deadline = time.monotonic() + wait_timeout
    while time.monotonic() < deadline:
        time.sleep(1.0)
        if probe(base_url, token):
            return True
    return False


def _build_messages(
    soul_md: str,
    instructions: str,
    input_text: str,
    conversation_history: list[dict] | None,
    memories: list[dict] | None,
) -> list[dict]:
    """组装 OpenAI 兼容 messages：system（人设+格式+记忆）+ 历史 + 本轮输入。"""
    system = (soul_md or "").strip() + "\n\n" + (instructions or "").strip()
    if memories:
        memory_text = "；".join(str(item.get("content", "")) for item in memories[-8:])
        if memory_text.strip():
            system += f"\n【用户记忆】{memory_text}"
    messages: list[dict] = [{"role": "system", "content": system.strip()}]
    if conversation_history:
        messages.extend(conversation_history[-14:])
    messages.append({"role": "user", "content": input_text})
    return messages


def _parse_sse_content(line: str) -> str:
    """解析单行 SSE data: payload，返回 delta 文本（非 delta 帧 / 空 content 返回 ""）。"""
    if not line.startswith("data:"):
        return ""
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return ""
    try:
        delta = json.loads(payload)["choices"][0]["delta"]
    except (json.JSONDecodeError, KeyError, IndexError):
        return ""
    return delta.get("content") or ""


def _chat_completions(
    *,
    base_url: str,
    token: str,
    model: str,
    messages: list[dict],
    timeout: float,
    on_delta: Callable[[str], None] = lambda _: None,
    max_tokens: int | None = None,
    temperature: float = 0.7,
) -> str:
    """POST /v1/chat/completions（stream:true），聚合并流式回传 delta 文本。

    Gateway 正常返回 SSE（text/event-stream）；若因代理配置返回普通 JSON，
    回退解析 choices[0].message.content。HTTP 错误 raise RuntimeError。
    """
    payload: dict = {
        "model": model, "messages": messages,
        "stream": True, "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base_url.rstrip('/')}/v1/chat/completions",
                headers=_auth_headers(token), json=payload,
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"OpenClaw Gateway 请求失败: {exc}") from exc
    if resp.is_error:
        raise RuntimeError(
            f"OpenClaw Gateway HTTP {resp.status_code}: {resp.text[:800]}"
        )

    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        # 非 SSE 回退：直接取完整 JSON 响应
        try:
            content = resp.json()["choices"][0]["message"]["content"] or ""
        except (json.JSONDecodeError, KeyError, IndexError) as exc:
            raise RuntimeError(f"OpenClaw 返回无法解析: {resp.text[:800]}") from exc
        if content:
            on_delta(content)
        return content

    # SSE 流式：逐行解析 data: 帧，聚合 delta
    content = ""
    for line in resp.text.splitlines():
        text = _parse_sse_content(line)
        if text:
            content += text
            on_delta(text)
    return content


def run_openclaw_turn(
    *,
    base_url: str,
    token: str,
    model: str,
    soul_md: str,
    instructions: str,
    input_text: str,
    conversation_history: list[dict] | None = None,
    memories: list[dict] | None = None,
    on_delta: Callable[[str], None] = lambda _: None,
    max_tokens: int | None = None,
    timeout: float = 300.0,
) -> str:
    """对话后端：整轮对话委托 OpenClaw 代理，流式返回最终文本。

    Raises:
        RuntimeError: HTTP 失败、网关不可达、返回无法解析。
    """
    messages = _build_messages(soul_md, instructions, input_text, conversation_history, memories)
    return _chat_completions(
        base_url=base_url, token=token, model=model, messages=messages,
        timeout=timeout, on_delta=on_delta, max_tokens=max_tokens,
    )


def run_gui_task(
    cfg: dict,
    task: str,
    on_status: Callable[[str], None] = lambda _: None,
) -> str:
    """CUA 工具入口：确保网关在线后把 GUI 任务委托给 OpenClaw 代理。

    返回代理回复文本；网关未启用/拉起失败/请求失败时返回降级提示文本
    （不抛异常，desktop_tools 的工具结果约定为文本）。
    """
    if not cfg.get("enabled"):
        return (
            "OpenClaw CUA 后端未启用。请在设置 → Agent 模式 → OpenClaw 勾选启用，"
            "并部署 OpenClaw Gateway（openclaw gateway，默认 127.0.0.1:18789）。"
        )
    base_url = str(cfg.get("base_url", "http://127.0.0.1:18789"))
    token = str(cfg.get("token", ""))
    model = str(cfg.get("model", "openclaw/default"))
    timeout = float(cfg.get("timeout", 120))

    if not ensure_gateway(
        base_url=base_url, token=token,
        autostart=bool(cfg.get("autostart", True)),
    ):
        return (
            f"OpenClaw Gateway 不可达（{base_url}）。请确认已安装 openclaw 并运行 openclaw gateway，"
            "或检查设置中的 Token 是否匹配 OPENCLAW_GATEWAY_TOKEN。"
        )

    on_status("🦞 OpenClaw 正在操作桌面…")
    try:
        reply = _chat_completions(
            base_url=base_url, token=token, model=model,
            messages=[{"role": "user", "content": task}],
            timeout=timeout,
        )
    except RuntimeError as exc:
        return f"OpenClaw Gateway 调用失败：{exc}"
    return reply or "OpenClaw 返回空回复。"
