"""Persistent state for the Amadeus terminal session."""
from __future__ import annotations

import json
from pathlib import Path
import uuid

from core.storage import APP_DIR


_STATE_PATH = APP_DIR / "terminal_state.json"
_MAX_HISTORY = 200


def load_terminal_state() -> dict:
    """Load terminal preferences without letting a damaged file break startup."""
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    history = data.get("history")
    if not isinstance(history, list):
        history = []
    data["history"] = [str(item) for item in history if str(item).strip()][-_MAX_HISTORY:]
    data["route"] = str(data.get("route") or "auto")
    data["cwd"] = str(data.get("cwd") or "")
    data["session_id"] = str(data.get("session_id") or f"amadeus-terminal-{uuid.uuid4().hex}")
    return data


def save_terminal_state(
    *, history: list[str], route: str, cwd: str | Path, session_id: str
) -> None:
    """Persist only terminal-local state; API credentials remain in config.json."""
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "history": [str(item) for item in history if str(item).strip()][-_MAX_HISTORY:],
            "route": route,
            "cwd": str(cwd),
            "session_id": session_id,
        }
        _STATE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
