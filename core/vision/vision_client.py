# core/vision_client.py
"""GPT-4o 视觉理解：屏幕帧 → 简短屏幕描述。

DeepSeek 无视觉能力，电话模式屏幕共享用 GPT-4o（用户额外配 key）。
未配 key 时返回空字符串，主管线降级为纯语音通话（spec §1.4 风险）。
"""
from __future__ import annotations

import base64
from io import BytesIO

import httpx
from PIL import Image


def frame_to_data_url(image_bytes: bytes, size: tuple[int, int] | None = None) -> str:
    """mss 截帧 BGRA bytes → base64 PNG data URL。

    best-effort：BGRA → PNG 转换失败时降级为直接 base64 编码原始 bytes
    （调用方 GPT-4o 对格式宽容，且 describe_screen 的 try/except 兜底）。
    """
    # mss 截帧是 BGRA，转 PNG
    try:
        width, height = size or _bgra_to_rgba_size(image_bytes)
        img = Image.frombytes("RGBA", (width, height), image_bytes, "raw", "BGRA")
        buf = BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:
        # 已经是 PNG/其他格式，直接 base64
        b64 = base64.b64encode(image_bytes).decode("ascii")
        return f"data:image/png;base64,{b64}"


def _bgra_to_rgba_size(image_bytes: bytes) -> tuple[int, int]:
    """根据 bytes 长度推算尺寸（mss 默认 1920x1080 BGRA = 8294400 bytes）。

    此函数是 best-effort，失败时由调用方降级。实际生产应从 mss 截帧对象拿 monitor 尺寸。
    """
    # 简化：假设 4 字节/像素，正方形不可能，这里仅给占位尺寸
    # 真实场景下 frame_to_data_url 的调用方应传入 (width, height)
    return (1, len(image_bytes) // 4) if image_bytes else (1, 1)


def frame_object_to_data_url(frame) -> str:
    """mss ScreenShot/bytes → PNG data URL，优先使用帧对象的真实宽高。"""
    if hasattr(frame, "bgra") and hasattr(frame, "width") and hasattr(frame, "height"):
        return frame_to_data_url(bytes(frame.bgra), (int(frame.width), int(frame.height)))
    if isinstance(frame, (bytes, bytearray)):
        return frame_to_data_url(bytes(frame))
    return frame_to_data_url(bytes(frame))


def describe_screen(
    image_bytes: bytes | object,
    endpoint: str,
    api_key: str,
    model: str,
    *,
    max_chars: int = 120,
) -> str:
    """屏幕帧 → 简短屏幕描述。失败/未配 key 返回空字符串。"""
    if not api_key or not image_bytes:
        return ""
    data_url = frame_object_to_data_url(image_bytes)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": f"用一句话（≤{max_chars}字）描述当前屏幕内容，聚焦用户正在做什么。"},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
        "stream": False,
        "max_tokens": 200,
    }
    try:
        resp = httpx.post(
            endpoint.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""
