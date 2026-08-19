"""lightweight_memory SQLite CRUD 测试。

覆盖：
- init_schema: 创建表/幂等性/创建目录
- record_greeting: 写入行/返回 id/source 字段
- last_greeting_ts: 返回最新/空表返回 None
- greeting_count_today: 本地日计数/边界
- recent_topics: 返回 set/空表/超时过滤
- similar_topic_exists: 存在/不存在/超时窗口
- record_feedback: 更新/不存在的 ID/记录时间戳
- clear_all: 清空/幂等
- CompanionStorage: 占位类存在
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from core.companion.storage import (
    CompanionStorage, init_schema, record_greeting, last_greeting_ts,
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


# === init_schema ===

def test_init_schema_creates_table(tmp_path, monkeypatch):
    """init_schema 应创建 lightweight_memory 表。"""
    db_path = tmp_path / "test_schema.db"
    monkeypatch.setattr("core.companion.storage.DB_PATH", db_path)
    init_schema()
    with sqlite3.connect(db_path) as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lightweight_memory'").fetchall()
    assert len(tables) == 1


def test_init_schema_creates_indexes(tmp_path, monkeypatch):
    """init_schema 应创建索引。"""
    db_path = tmp_path / "test_idx.db"
    monkeypatch.setattr("core.companion.storage.DB_PATH", db_path)
    init_schema()
    with sqlite3.connect(db_path) as conn:
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_lm%'").fetchall()
    index_names = [r[0] for r in indexes]
    assert "idx_lm_ts" in index_names
    assert "idx_lm_source_topic" in index_names


def test_init_schema_is_idempotent(tmp_path, monkeypatch):
    """多次调用 init_schema 不应报错。"""
    db_path = tmp_path / "test_idem.db"
    monkeypatch.setattr("core.companion.storage.DB_PATH", db_path)
    init_schema()
    init_schema()  # 不应抛异常
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM lightweight_memory").fetchone()[0]
    assert count == 0


def test_init_schema_creates_parent_dir(tmp_path, monkeypatch):
    """init_schema 应创建父目录。"""
    db_path = tmp_path / "deep" / "nested" / "dir" / "memory.db"
    monkeypatch.setattr("core.companion.storage.DB_PATH", db_path)
    init_schema()
    assert db_path.parent.exists()


# === record_greeting 返回 id ===

def test_record_greeting_returns_row_id(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    gid = record_greeting("hello", "idle", "idle")
    assert isinstance(gid, int)
    assert gid > 0


def test_record_greeting_ids_are_sequential(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    gid1 = record_greeting("first", "idle", "idle")
    gid2 = record_greeting("second", "work", "concern")
    assert gid2 == gid1 + 1


def test_record_greeting_source_is_companion(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    record_greeting("text", "topic", "emotion")
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        row = conn.execute("SELECT source FROM lightweight_memory").fetchone()
    assert row[0] == "companion"


# === last_greeting_ts 空表 ===

def test_last_greeting_ts_returns_none_when_empty(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    assert last_greeting_ts() is None


def test_last_greeting_ts_returns_only_one_row(tmp_path, monkeypatch):
    """空表时 last_greeting_ts 返回 None，有数据时返回字符串。"""
    _make_storage(tmp_path, monkeypatch)
    assert last_greeting_ts() is None
    record_greeting("hello", "idle", "idle")
    result = last_greeting_ts()
    assert result is not None
    assert isinstance(result, str)


# === greeting_count_today 边界 ===

def test_greeting_count_today_zero_when_empty(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    assert greeting_count_today() == 0


def test_greeting_count_today_counts_multiple(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    record_greeting("a", "idle", "idle")
    record_greeting("b", "work", "concern")
    record_greeting("c", "tease", "tease")
    assert greeting_count_today() == 3


# === recent_topics 边界 ===

def test_recent_topics_empty_set_when_no_data(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    topics = recent_topics(hours=2)
    assert isinstance(topics, set)
    assert len(topics) == 0


def test_recent_topics_filters_by_hours(tmp_path, monkeypatch):
    """超出 hours 窗口的主题不应返回。"""
    _make_storage(tmp_path, monkeypatch)
    # 写入一条 3 小时前的问候
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with patch("core.companion.storage.datetime") as m:
        m.now.return_value.isoformat.return_value = old_ts
        record_greeting("old", "old_topic", "idle")
    # 写入一条刚刚的问候
    record_greeting("new", "new_topic", "idle")
    topics = recent_topics(hours=2)
    assert "new_topic" in topics
    assert "old_topic" not in topics


def test_recent_topics_returns_distinct(tmp_path, monkeypatch):
    """同主题多次问候应去重。"""
    _make_storage(tmp_path, monkeypatch)
    record_greeting("a", "idle", "idle")
    record_greeting("b", "idle", "concern")
    record_greeting("c", "idle", "tease")
    topics = recent_topics(hours=2)
    assert topics == {"idle"}


# === similar_topic_exists 边界 ===

def test_similar_topic_exists_empty_table(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    assert similar_topic_exists("idle", hours=6) is False


def test_similar_topic_exists_filters_by_hours(tmp_path, monkeypatch):
    """超出 hours 窗口的同主题应返回 False。"""
    _make_storage(tmp_path, monkeypatch)
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    with patch("core.companion.storage.datetime") as m:
        m.now.return_value.isoformat.return_value = old_ts
        record_greeting("old", "idle", "idle")
    # 7h 前写入的，6h 窗口不包含
    assert similar_topic_exists("idle", hours=6) is False
    # 8h 窗口包含
    assert similar_topic_exists("idle", hours=8) is True


def test_similar_topic_exists_none_topic(tmp_path, monkeypatch):
    """写入 None topic 时不应匹配。"""
    _make_storage(tmp_path, monkeypatch)
    record_greeting("text", None, "idle")
    assert similar_topic_exists(None, hours=6) is False


# === record_feedback 边界 ===

def test_record_feedback_on_nonexistent_id_does_not_error(tmp_path, monkeypatch):
    """对不存在的 ID 写 feedback 不应报错（SQLite UPDATE 0 rows）。"""
    _make_storage(tmp_path, monkeypatch)
    record_feedback(99999, "positive")  # 不存在
    # 不抛异常即通过


def test_record_feedback_records_timestamp(tmp_path, monkeypatch):
    """feedback 应同时记录 feedback_ts。"""
    _make_storage(tmp_path, monkeypatch)
    gid = record_greeting("greeting", "idle", "idle")
    record_feedback(gid, "positive")
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        row = conn.execute(
            "SELECT user_feedback, feedback_ts FROM lightweight_memory WHERE id=?", (gid,)
        ).fetchone()
    assert row[0] == "positive"
    assert row[1] is not None
    assert isinstance(row[1], str)


def test_record_feedback_overwrites_previous(tmp_path, monkeypatch):
    """多次 feedback 应覆盖之前的值。"""
    _make_storage(tmp_path, monkeypatch)
    gid = record_greeting("greeting", "idle", "idle")
    record_feedback(gid, "positive")
    record_feedback(gid, "negative")
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        row = conn.execute("SELECT user_feedback FROM lightweight_memory WHERE id=?", (gid,)).fetchone()
    assert row[0] == "negative"


# === clear_all 边界 ===

def test_clear_all_on_empty_table(tmp_path, monkeypatch):
    """空表 clear_all 不应报错。"""
    _make_storage(tmp_path, monkeypatch)
    clear_all()  # 不抛异常
    assert greeting_count_today() == 0


def test_clear_all_is_idempotent(tmp_path, monkeypatch):
    _make_storage(tmp_path, monkeypatch)
    record_greeting("a", "idle", "idle")
    clear_all()
    clear_all()  # 再次清空空表
    assert greeting_count_today() == 0


# === CompanionStorage 占位类 ===

def test_companion_storage_class_exists():
    """CompanionStorage 占位类应可实例化。"""
    obj = CompanionStorage()
    assert obj is not None
