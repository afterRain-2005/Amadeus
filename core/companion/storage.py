"""lightweight_memory SQLite 存储。

与未来 P1 记忆层（facts/episodes/向量）同库 data/memory.db，
schema 兼容 P1 episodes 表（保留 source 字段区分来源）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from core.storage import APP_DIR

DB_PATH: Path = APP_DIR / "memory.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema() -> None:
    """创建表（若不存在）。启动时调用一次。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS lightweight_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            source TEXT NOT NULL,
            text TEXT NOT NULL,
            topic TEXT,
            emotion TEXT,
            user_feedback TEXT,
            feedback_ts TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lm_ts ON lightweight_memory(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lm_source_topic ON lightweight_memory(source, topic)")


def record_greeting(text: str, topic: str, emotion: str) -> int:
    """写入一次 companion 问候，返回新行 id。"""
    ts = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO lightweight_memory (ts, source, text, topic, emotion) VALUES (?, ?, ?, ?, ?)",
            (ts, "companion", text, topic, emotion),
        )
        return cur.lastrowid


def record_feedback(greeting_id: int, feedback: str) -> None:
    """记录用户对某条问候的反馈（'positive'|'negative'|'neutral'）。"""
    ts = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "UPDATE lightweight_memory SET user_feedback=?, feedback_ts=? WHERE id=?",
            (feedback, ts, greeting_id),
        )


def last_greeting_ts() -> Optional[str]:
    """最近一次 companion 问候的 ISO8601 时间戳，无则 None。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT ts FROM lightweight_memory WHERE source='companion' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return row["ts"] if row else None


def greeting_count_today() -> int:
    """今天（UTC）已问候次数。"""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM lightweight_memory WHERE source='companion' AND ts >= ?",
            (today_start,),
        ).fetchone()
    return row["c"]


def recent_topics(hours: int = 2) -> set[str]:
    """最近 N 小时内 companion 问候过的主题集合（去重）。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT topic FROM lightweight_memory WHERE source='companion' AND ts >= ?",
            (cutoff,),
        ).fetchall()
    return {r["topic"] for r in rows if r["topic"]}


def similar_topic_exists(topic: str, hours: int = 6) -> bool:
    """最近 N 小时内是否已问候过同主题。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM lightweight_memory WHERE source='companion' AND topic=? AND ts >= ? LIMIT 1",
            (topic, cutoff),
        ).fetchone()
    return row is not None


def clear_all() -> None:
    """清空 lightweight_memory 表（设置页"清空记忆"按钮调用）。"""
    with _connect() as conn:
        conn.execute("DELETE FROM lightweight_memory")


class CompanionStorage:
    """模块级函数 API 的命名空间占位类。

    plan 测试模块 import 列表中包含 CompanionStorage 名称，但当前阶段所有
    CRUD 都通过模块级函数访问（DB_PATH 单例 + monkeypatch 覆盖即可测），
    此类仅作命名空间占位让 import 通过，P1 阶段若需实例化封装（如多用户
    /多角色隔离）再扩展为真正的类。
    """
    pass
