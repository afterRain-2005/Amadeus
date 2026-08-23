"""运行时诊断日志（从 desktop_pet.py 提出）。"""
from __future__ import annotations

from pathlib import Path

from core.storage import APP_DIR as _APP_DIR


def _write_runtime_log(filename: str, content: str) -> Path | None:
    """Write a UTF-8 diagnostic log beside runtime data."""
    try:
        log_dir = _APP_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / filename
        path.write_text(content, encoding="utf-8")
        return path
    except OSError:
        return None
