"""lightweight_memory SQLite CRUD 测试。"""
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from core.companion.storage import (
    CompanionStorage, record_greeting, last_greeting_ts,
    greeting_count_today, recent_topics, similar_topic_exists,
    record_feedback, clear_all,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_storage(tmp_path, monkeypatch):
    """用临时 db 文件，避免污染真实 data/memory.db。"""
    db_path = tmp_path / "memory.db"
    monkeypatch.setattr("core.companion.storage.DB_PATH", db_path)
    # 初始化 schema
    with sqlite3.connect(db_path) as conn:
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
    return db_path


def test_record_greeting_writes_row(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    record_greeting(text="你好啊", topic="idle", emotion="idle")
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        rows = list(conn.execute("SELECT text, topic, emotion, source FROM lightweight_memory"))
    assert rows == [("你好啊", "idle", "idle", "companion")]


def test_last_greeting_ts_returns_latest(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    ts1 = _utc_now_iso()
    with patch("core.companion.storage.datetime") as m:
        m.now.return_value.isoformat.return_value = ts1
        record_greeting("first", "idle", "idle")
    ts2 = "2099-12-31T23:59:59+00:00"
    with patch("core.companion.storage.datetime") as m:
        m.now.return_value.isoformat.return_value = ts2
        record_greeting("second", "work", "concern")
    assert last_greeting_ts() == ts2


def test_greeting_count_today_counts_only_today(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    # 昨天的问候
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with patch("core.companion.storage.datetime") as m:
        m.now.return_value.isoformat.return_value = yesterday
        record_greeting("old", "idle", "idle")
    # 今天的两个
    record_greeting("new1", "idle", "idle")
    record_greeting("new2", "work", "concern")
    assert greeting_count_today() == 2


def test_recent_topics_returns_set(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    record_greeting("a", "idle", "idle")
    record_greeting("b", "work", "concern")
    record_greeting("c", "tease", "tease")
    topics = recent_topics(hours=2)
    assert isinstance(topics, set)
    assert "idle" in topics
    assert "work" in topics
    assert "tease" in topics


def test_similar_topic_exists_within_window(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    record_greeting("old idle greeting", "idle", "idle")
    assert similar_topic_exists("idle", hours=6) is True
    assert similar_topic_exists("work", hours=6) is False


def test_record_feedback_updates_row(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    record_greeting("greeting", "idle", "idle")
    # 取最新一条的 id
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        gid = conn.execute("SELECT id FROM lightweight_memory ORDER BY id DESC LIMIT 1").fetchone()[0]
    record_feedback(gid, "negative")
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        row = conn.execute("SELECT user_feedback FROM lightweight_memory WHERE id=?", (gid,)).fetchone()
    assert row[0] == "negative"


def test_clear_all_empties_table(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    record_greeting("a", "idle", "idle")
    record_greeting("b", "work", "concern")
    clear_all()
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        count = conn.execute("SELECT COUNT(*) FROM lightweight_memory").fetchone()[0]
    assert count == 0
