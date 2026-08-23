"""轻量级自动路由层：用本地 Ollama 小模型做二分类分流。

将用户输入分为两类：
- local：桌面/GUI 操作，或普通闲聊、简单问答（本地直连兜底）
- harness：复杂代码/工程任务（DeepSeek Harness）

任何异常（Ollama 不可达、超时、非法返回）一律回退 "local"。
"""
from __future__ import annotations

import re

import httpx

_ROUTE_SYSTEM = (
    "你是路由分类器，判断用户消息该由哪个后端处理，只输出一个词，不要解释。\n"
    "- local：需要操作桌面 GUI（打开/关闭应用或浏览器、点击、输入文字、截图、窗口管理、鼠标键盘），或普通闲聊、简单问答。\n"
    "- harness：需要复杂代码/工程任务（编写或修改代码、运行命令、文件编辑、多步骤编程、子代理/工作流）。\n"
    "输出只能是 local 或 harness。"
)

_VALID_TARGETS = ("local", "harness")


def route_with_ollama(
    text: str,
    *,
    targets: list[str],
    base_url: str,
    model: str,
    timeout: float = 30,
) -> str:
    """把 ``text`` 分类到 ``targets`` 中的某个模式，失败回退 "local"。

    - 无有效目标或未勾选任何目标 -> "local"
    - 仅勾选一个目标 -> 直接返回该目标，不调 Ollama
    - 勾选多个目标 -> 调 Ollama 二分类，返回 "local" 或 "harness"
    """
    valid = [t for t in (targets or []) if t in _VALID_TARGETS]
    if not valid:
        return "local"
    if len(valid) == 1:
        return valid[0]
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base_url.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": _ROUTE_SYSTEM},
                        {"role": "user", "content": text},
                    ],
                },
            )
        if resp.is_error:
            return "local"
        content = resp.json().get("message", {}).get("content", "")
        match = re.search(r"\b(local|harness)\b", str(content).lower())
        if not match:
            return "local"
        route = match.group(1)
        return route if route in valid else "local"
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        return "local"
