"""应用版本号与远程版本检查。

版本检查采用“拉取纯文本版本字符串”策略，URL 可在设置页配置。
未配置 URL 时跳过检查（自用场景默认无远程源）。
"""
from __future__ import annotations

import urllib.request
from typing import Optional

__version__ = "0.3.2"


def parse_version(text: str) -> tuple[int, int, int]:
    """把 '0.2.0' 解析为 (0, 2, 0)。非法格式抛 ValueError。"""
    parts = text.strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"非法版本号：{text!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def check_latest_version(url: Optional[str]) -> Optional[str]:
    """从 url 拉取最新版本字符串（纯文本，首行）。

    无 url 或网络失败时返回 None，绝不抛异常。
    """
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        return text.splitlines()[0].strip() if text.strip() else None
    except (OSError, ValueError, IndexError):
        return None
