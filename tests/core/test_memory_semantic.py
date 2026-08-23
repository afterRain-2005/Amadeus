# tests/test_memory_semantic.py — 语义记忆检索（core/memory.py）
# 覆盖：关键词兜底（无 semantic 配置）/ 语义排序 / 惰性向量化写回 /
#       失败退避降级 / 旧库迁移补列。
import sqlite3

import pytest

from core import memory


@pytest.fixture()
def mem_db(tmp_path, monkeypatch):
    """把 memory.DB_PATH 指到临时目录，并清理进程级缓存/退避状态。"""
    monkeypatch.setattr(memory, "DB_PATH", tmp_path / "memory.db")
    monkeypatch.setattr(memory, "_VEC_CACHE", {})
    monkeypatch.setattr(memory, "_embed_fail_until", 0.0)
    memory.init_schema()
    yield memory


def _fake_embed_factory(vectors_by_text):
    """按文本精确匹配返回向量的假 embed_texts（未命中 → 单位向量）。"""

    def fake(texts, *, endpoint, api_key, model):
        out = []
        for t in texts:
            vec = vectors_by_text.get(t)
            if vec is None:
                # 未命中给一个与查询正交的向量，避免偶然相似
                vec = [-1.0] * (len(next(iter(vectors_by_text.values()), [0.0])))
            out.append(list(vec))
        return out

    return fake


def test_keyword_fallback_without_semantic(mem_db):
    memory.remember("用户喜欢拉面", kind="fact", weight=1.4)
    memory.remember("用户住在东京", kind="fact", weight=1.4)
    items = mem_db.recall(query="拉面", limit=2)  # semantic=None → 关键词路径
    assert items
    assert items[0]["content"] == "用户喜欢拉面"


def test_semantic_ranking(monkeypatch, mem_db):
    # 三条记忆：两条有语义关联，一条无关
    memory.remember("用户养了一只叫麻美的猫", kind="fact", weight=1.0)
    memory.remember("用户的生日是 12 月 8 日", kind="fact", weight=1.0)
    memory.remember("用户讨厌香菜", kind="fact", weight=1.0)
    fake = _fake_embed_factory({
        "用户养了一只叫麻美的猫": [1.0, 0.0],
        "用户的生日是 12 月 8 日": [0.6, 0.8],
        "用户讨厌香菜": [0.0, 1.0],
        "宠物": [1.0, 0.0],   # 查询与"猫"记忆同向
    })
    monkeypatch.setattr(mem_db, "embed_texts", fake)
    items = mem_db.recall(
        query="宠物",
        limit=3,
        semantic={"endpoint": "http://x/v1", "api_key": "k", "model": "m"},
    )
    # 与查询同向的猫记忆应排第一，正交的香菜记忆垫底
    assert items[0]["content"] == "用户养了一只叫麻美的猫"
    assert items[-1]["content"] == "用户讨厌香菜"


def test_lazy_vectors_persisted(monkeypatch, mem_db):
    memory.remember("记住明天开会", kind="fact", weight=1.0)
    fake = _fake_embed_factory({"明天开会": [1.0, 0.0]})
    calls = []

    def counting(texts, **kw):
        calls.append(list(texts))
        return fake(texts, **kw)

    monkeypatch.setattr(mem_db, "embed_texts", counting)
    sem = {"endpoint": "http://x/v1", "api_key": "k", "model": "m"}
    mem_db.recall(query="开会提醒", semantic=sem)
    with sqlite3.connect(mem_db.DB_PATH) as conn:
        row = conn.execute("SELECT embedding FROM hermes_memory").fetchone()
    assert row[0] is not None, "缺失向量应在 recall 时惰性补算并写回"
    # 第二次 recall 不再重复请求该条向量（DB 命中 + 进程缓存）
    before = len(calls)
    mem_db.recall(query="开会提醒", semantic=sem)
    joined = "".join(t for batch in calls[before:] for t in batch)
    assert "记住明天开会" not in joined


def test_failure_backoff_degrades_to_keyword(monkeypatch, mem_db):
    memory.remember("用户喜欢咖啡", kind="fact", weight=1.4)

    def boom(texts, *, endpoint, api_key, model):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(mem_db, "embed_texts", boom)
    sem = {"endpoint": "http://x/v1", "api_key": "k", "model": "m"}
    items = mem_db.recall(query="咖啡", semantic=sem)
    assert items and items[0]["content"] == "用户喜欢咖啡"
    # 退避期内直接走关键词路径（不再触发 embed_texts）
    after = []

    def spy(texts, **kw):
        after.append(texts)
        return [[] for _ in texts]

    monkeypatch.setattr(mem_db, "embed_texts", spy)
    items2 = mem_db.recall(query="咖啡", semantic=sem)
    assert not after, "退避期内不应再发起 embedding 请求"
    assert items2 and items2[0]["content"] == "用户喜欢咖啡"


def test_migration_adds_embedding_columns(tmp_path, monkeypatch):
    db = tmp_path / "memory.db"
    monkeypatch.setattr(memory, "DB_PATH", db)
    # 模拟旧库：无 embedding 列
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE hermes_memory (
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
        conn.execute(
            "INSERT INTO hermes_memory (kind, content, source, created_at, updated_at, last_seen_at)"
            " VALUES ('fact', '旧记忆', 'chat', '2026-01-01', '2026-01-01', '2026-01-01')"
        )
    memory.init_schema()
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(hermes_memory)")}
        row = conn.execute("SELECT content FROM hermes_memory").fetchone()
    assert {"embedding", "embedding_model"} <= cols
    assert row == ("旧记忆",)


def test_semantic_config_fallback_to_top_level():
    cfg = {
        "endpoint": "https://api.example.com/v1",
        "api_key": "sk-top",
        "memory": {"semantic": {"enabled": True}},
    }
    resolved = memory.semantic_config(cfg)
    assert resolved == {
        "endpoint": "https://api.example.com/v1",
        "api_key": "sk-top",
        "model": "text-embedding-v4",
    }
    assert memory.semantic_config({"memory": {"semantic": {"enabled": False}}}) is None
    assert memory.semantic_config({}) is None  # 无端点 → 禁用
