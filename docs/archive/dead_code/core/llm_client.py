"""OpenAI-compatible streaming chat client."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json

import httpx


# ============================================================
# 函数：stream_chat()
# 作用：★流式对话核心函数。调用 OpenAI 兼容的 /chat/completions 接口，
#       以 SSE（Server-Sent Events，服务器逐块推送）方式接收 AI 回复。
#       每收到一个字（delta）就立即通过 on_delta 回调吐出去，
#       同时拼接进 full 变量，最后返回完整回复。
#       这是"打字机效果"（气泡里一个字一个字蹦出来）的实现原理。
# 参数：
#   endpoint    str        接口地址（如 http://127.0.0.1:8642）
#   api_key     str        API 密钥
#   model       str        模型名
#   personality str        人设文本（拼进 system 消息）
#   history     list[dict] 对话历史（取最近 12 条）
#   on_delta    callable   流式回调：on_delta(增量文本)，每收到一段就调用
#   memories    list[dict]|None 长期记忆（取最近 8 条拼进上下文）
# 返回值：str —— AI 的完整回复文本
# ============================================================
def stream_chat(
    *, endpoint: str, api_key: str, model: str, personality: str,
    history: list[dict], on_delta: Callable[[str], None], memories: list[dict] | None = None,
) -> str:
    now = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    memory_text = "；".join(item.get("content", "") for item in (memories or [])[-8:])
    context = f"{personality}\n\n【当前时间】{now}"
    if memory_text:
        context += f"\n\n【关于用户的记忆】{memory_text}"
    messages = [{"role": "system", "content": context}, *history[-12:]]
    url = endpoint.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages, "max_tokens": 512, "stream": True}
    full = ""
    with httpx.stream(
        "POST", url, headers={"Authorization": f"Bearer {api_key}"}, json=payload,
        timeout=httpx.Timeout(90, connect=15),
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                delta = json.loads(data).get("choices", [{}])[0].get("delta", {}).get("content", "")
            except (json.JSONDecodeError, IndexError, AttributeError):
                continue
            if delta:
                full += delta
                on_delta(delta)
    return full
