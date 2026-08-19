"""Shared Hermes-like long-term memory for all conversation modes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import sqlite3
from pathlib import Path

from core.storage import APP_DIR


DB_PATH: Path = APP_DIR / "memory.db"


@dataclass(frozen=True)
class MemoryRecord:
    id: int
    kind: str
    content: str
    source: str
    scope: str
    weight: float
    created_at: str
    updated_at: str

    def as_prompt_item(self) -> dict:
        return {
            "type": self.kind,
            "content": self.content,
            "source": self.source,
            "scope": self.scope,
            "weight": self.weight,
            "created_at": self.created_at,
        }


_FACT_PATTERNS = [
    re.compile(r"(?:我叫|我的名字是|叫我|I am|I'm|my name is)\s*([^，。,.!?！？\s]{1,32})", re.I),
    re.compile(r"(?:我喜欢|我愛|我爱|I like|I love)\s*([^，。,.!?！？]{1,60})", re.I),
    re.compile(r"(?:我讨厌|我不喜欢|I hate|I dislike)\s*([^，。,.!?！？]{1,60})", re.I),
    re.compile(r"(?:记住|remember that)\s*([^。.!！?？]{2,120})", re.I),
]


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema() -> None:
    with _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS hermes_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'global',
                weight REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 1,
                UNIQUE(kind, content, scope)
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hm_scope_weight ON hermes_memory(scope, weight)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hm_updated_at ON hermes_memory(updated_at)")


def remember(content: str, *, kind: str = "fact", source: str = "chat", scope: str = "global", weight: float = 1.0) -> int:
    text = _normalize(content)
    if not text:
        return 0
    init_schema()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, weight, hit_count FROM hermes_memory WHERE kind=? AND content=? AND scope=?",
            (kind, text, scope),
        ).fetchone()
        if row:
            new_weight = min(3.0, max(float(row["weight"]), weight) + 0.05)
            conn.execute(
                """UPDATE hermes_memory
                   SET source=?, weight=?, updated_at=?, last_seen_at=?, hit_count=?
                   WHERE id=?""",
                (source, new_weight, now, now, int(row["hit_count"]) + 1, int(row["id"])),
            )
            return int(row["id"])
        cur = conn.execute(
            """INSERT INTO hermes_memory
               (kind, content, source, scope, weight, created_at, updated_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (kind, text, source, scope, weight, now, now, now),
        )
        return int(cur.lastrowid)


def recall(*, query: str = "", limit: int = 8, scope: str = "global") -> list[dict]:
    init_schema()
    terms = _query_terms(query)
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM hermes_memory
               WHERE scope IN (?, 'global')
               ORDER BY weight DESC, updated_at DESC
               LIMIT ?""",
            (scope, max(limit * 4, limit)),
        ).fetchall()
    records = [_row_to_record(row) for row in rows]
    if terms:
        records.sort(key=lambda item: (_score(item.content, terms), item.weight, item.updated_at), reverse=True)
    return [record.as_prompt_item() for record in records[:limit]]


def remember_turn(*, user_text: str, assistant_text: str = "", source: str = "chat", scope: str = "global") -> list[int]:
    ids: list[int] = []
    for fact in extract_facts(user_text):
        memory_id = remember(fact, kind="fact", source=source, scope=scope, weight=1.4)
        if memory_id:
            ids.append(memory_id)
    if assistant_text:
        summary = _summarize_episode(user_text, assistant_text)
        if summary:
            ids.append(remember(summary, kind="episode", source=source, scope=scope, weight=0.6))
    return ids


def merge_memories(local_memories: list[dict] | None, recalled: list[dict] | None, *, limit: int = 12) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for item in [*(local_memories or []), *(recalled or [])]:
        content = _normalize(str(item.get("content", "")) if isinstance(item, dict) else str(item))
        if not content or content in seen:
            continue
        seen.add(content)
        if isinstance(item, dict):
            copied = dict(item)
            copied["content"] = content
        else:
            copied = {"type": "fact", "content": content}
        merged.append(copied)
    return merged[-limit:]


def clear_all() -> None:
    init_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM hermes_memory")


def extract_facts(text: str) -> list[str]:
    facts: list[str] = []
    for pattern in _FACT_PATTERNS:
        for match in pattern.finditer(text or ""):
            fact = _normalize(match.group(0))
            if fact and fact not in facts:
                facts.append(fact)
    return facts


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=int(row["id"]),
        kind=str(row["kind"]),
        content=str(row["content"]),
        source=str(row["source"]),
        scope=str(row["scope"]),
        weight=float(row["weight"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _query_terms(query: str) -> set[str]:
    return {term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]{2,}", query or "")}


def _score(content: str, terms: set[str]) -> int:
    lowered = content.lower()
    return sum(1 for term in terms if term in lowered)


def _summarize_episode(user_text: str, assistant_text: str) -> str:
    user = _normalize(user_text)
    assistant = _normalize(assistant_text)
    if len(user) < 8:
        return ""
    if len(assistant) > 90:
        assistant = assistant[:87] + "..."
    return f"User asked: {user[:120]} / Assistant replied: {assistant}"
