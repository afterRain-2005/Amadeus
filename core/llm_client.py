"""OpenAI-compatible streaming chat client."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json

import httpx


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
