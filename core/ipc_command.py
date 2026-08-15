"""桌宠 overlay→renderer 命令的序列化与 JS 应用（纯函数，无第三方依赖）。

命令走 mp.Pipe(duplex=True) 反向通道，替代旧的 pet_command.json 文件轮询。
"""
from __future__ import annotations
from typing import Any


def serialize_command(**payload: Any) -> tuple[str, dict]:
    """把 emotion/speaking 等关键字段打包成管道消息。"""
    return ("command", payload)


def apply_command_js(payload: dict) -> str:
    """把命令 payload 翻译成 Live2D 页面可执行的 JS 语句（多条用换行拼接）。

    空 payload 返回空串。
    """
    lines: list[str] = []
    if "emotion" in payload:
        lines.append(f"window.__amadeus.setEmotion({payload['emotion']!r})")
    if "speaking" in payload:
        value = "true" if payload["speaking"] else "false"
        lines.append(f"window.__amadeus.setSpeaking({value})")
    return "\n".join(lines)
