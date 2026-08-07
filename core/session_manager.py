"""Per-character conversation sessions and lightweight memories."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import uuid

from core.storage import APP_DIR


def _path(character_id: str) -> Path:
    path = APP_DIR / "characters" / character_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "sessions.json"


def _new_session(greeting: str = "") -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    messages = [{"role": "assistant", "content": greeting}] if greeting else []
    return {"id": uuid.uuid4().hex, "name": "新对话", "created_at": now,
            "updated_at": now, "messages": messages, "memories": []}


def load_state(character_id: str, greeting: str = "") -> dict:
    path = _path(character_id)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        session = _new_session(greeting)
        state = {"active_id": session["id"], "sessions": [session]}
        save_state(character_id, state)
    if not state.get("sessions"):
        session = _new_session(greeting)
        state = {"active_id": session["id"], "sessions": [session]}
    return state


def save_state(character_id: str, state: dict) -> None:
    _path(character_id).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def active_session(state: dict) -> dict:
    return next((s for s in state["sessions"] if s["id"] == state["active_id"]), state["sessions"][0])


def create_session(state: dict, greeting: str) -> dict:
    session = _new_session(greeting)
    state["sessions"].insert(0, session)
    state["active_id"] = session["id"]
    return session


def add_message(session: dict, role: str, content: str, image_path: str = "") -> None:
    message = {"role": role, "content": content}
    if image_path:
        message["image_path"] = image_path
    session["messages"].append(message)
    session["updated_at"] = datetime.now().isoformat(timespec="seconds")
    if session["name"] == "新对话" and role == "user" and content:
        session["name"] = re.sub(r"\s+", " ", content)[:18]
    if role == "user":
        extract_local_memories(session, content)


def extract_local_memories(session: dict, text: str) -> None:
    patterns = [r"我叫([^，。！？\s]{1,16})", r"我喜欢([^，。！？]{1,30})", r"我是([^，。！？]{1,30})"]
    existing = {item["content"] for item in session.get("memories", [])}
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            fact = match.group(0)
            if fact not in existing:
                session.setdefault("memories", []).append({"type": "fact", "content": fact})


def export_session(session: dict, character_name: str, destination: Path) -> None:
    lines = [f"角色：{character_name}", f"会话：{session['name']}", ""]
    for message in session["messages"]:
        name = "你" if message["role"] == "user" else character_name
        lines.extend([f"[{name}]", message["content"], ""])
    destination.write_text("\n".join(lines), encoding="utf-8")
