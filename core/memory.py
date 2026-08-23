"""Shared Hermes-like long-term memory for all conversation modes.

检索策略（airi Memory Alaya 风格的两级召回）：
1. 语义检索（可选）：OpenAI 兼容 /embeddings 接口把记忆内容与查询向量化，
   余弦相似度排序；向量惰性计算（recall 时补算候选行并写回 DB）。
2. 关键词匹配（兜底）：语义不可用（未配置/请求失败）时退回词重叠打分，
   与旧版行为完全一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
import sqlite3
import struct
import time
from pathlib import Path

import httpx

from core.storage import APP_DIR


DB_PATH: Path = APP_DIR / "memory.db"

# === 语义检索（embedding）默认参数 ===
# endpoint 留空时回退顶层对话 endpoint/api_key（SiliconFlow/Ollama 等 OpenAI 兼容端点
# 大多提供 /embeddings）；不支持时首次请求失败即进入 10 分钟退避，自动降级关键词。
SEMANTIC_DEFAULTS: dict[str, object] = {
    "enabled": True,
    "endpoint": "",
    "api_key": "",
    "model": "text-embedding-v4",
}
# 单次 recall 最多补算多少条缺失向量（控制首轮回包延迟）
_EMBED_BATCH_CAP = 12
# 请求失败后的退避秒数（期间直接走关键词路径）
_EMBED_BACKOFF_SECONDS = 600.0

_embed_fail_until = 0.0
_VEC_CACHE: dict[tuple[str, str], list[float]] = {}


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
    embedding: bytes | None = None

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
        # 旧库迁移：语义检索列（CREATE IF NOT EXISTS 不会给已存在的表补列）
        cols = {row[1] for row in conn.execute("PRAGMA table_info(hermes_memory)")}
        if "embedding" not in cols:
            conn.execute("ALTER TABLE hermes_memory ADD COLUMN embedding BLOB")
        if "embedding_model" not in cols:
            conn.execute("ALTER TABLE hermes_memory ADD COLUMN embedding_model TEXT")
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


def recall(*, query: str = "", limit: int = 8, scope: str = "global", semantic: dict | None = None) -> list[dict]:
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
        ranked = _rank_semantic(records, query, terms, semantic) if (semantic and query) else None
        if ranked is not None:
            records = ranked
        else:
            records.sort(key=lambda item: (_score(item.content, terms), item.weight, item.updated_at), reverse=True)
    return [record.as_prompt_item() for record in records[:limit]]


# ============================================================
# 函数：_rank_semantic()
# 作用：语义检索排序。候选记忆缺失向量时惰性补算（批量 ≤ _EMBED_BATCH_CAP
#       条并写回 DB），再与查询向量做余弦相似度。
#       排序分 = cos + 0.03*关键词命中 + 0.01*weight；
#       无向量条目给 0.15 基线 + 关键词/weight 加成（新记忆逐步被向量化，
#       期间不至沉底）。任何异常返回 None → caller 回退关键词路径。
# 参数：
#   records list[MemoryRecord] 候选记忆（DB 顺序）
#   query   str 用户查询原文
#   terms   set[str] 查询词元（兜底加成用）
#   semantic dict|None {"endpoint","api_key","model"}
# 返回值：list[MemoryRecord] | None —— None 表示语义不可用
# ============================================================
def _rank_semantic(
    records: list[MemoryRecord],
    query: str,
    terms: set[str],
    semantic: dict,
) -> list[MemoryRecord] | None:
    global _embed_fail_until
    if time.time() < _embed_fail_until:
        return None
    endpoint = str(semantic.get("endpoint") or "").strip()
    api_key = str(semantic.get("api_key") or "")
    model = str(semantic.get("model") or "")
    if not endpoint or not model:
        return None

    def kw_bonus(record: MemoryRecord) -> float:
        return 0.03 * _score(record.content, terms) + 0.01 * record.weight

    try:
        # 1) 补算缺失向量（限流，写回 DB，下次 recall 直接命中）
        missing = [r for r in records if r.embedding is None][:_EMBED_BATCH_CAP]
        vectors: dict[int, list[float]] = {}
        if missing:
            fresh = embed_texts([r.content for r in missing], endpoint=endpoint, api_key=api_key, model=model)
            with _connect() as conn:
                for record, vec in zip(missing, fresh):
                    if not vec:
                        continue
                    conn.execute(
                        "UPDATE hermes_memory SET embedding=?, embedding_model=? WHERE id=?",
                        (_pack(vec), model, record.id),
                    )
                    vectors[record.id] = vec
        # 2) 查询向量
        query_vecs = embed_texts([query], endpoint=endpoint, api_key=api_key, model=model)
        qvec = query_vecs[0] if query_vecs else []
        if not qvec:
            return None
        # 3) 余弦相似度排序
        scored: list[tuple[float, int, MemoryRecord]] = []
        for index, record in enumerate(records):
            vec = vectors.get(record.id)
            if vec is None and record.embedding is not None:
                try:
                    cached = _VEC_CACHE.get((model, _vec_key(record.content)))
                    vec = cached if cached is not None else _unpack(record.embedding)
                    _VEC_CACHE[(model, _vec_key(record.content))] = vec
                except Exception:
                    vec = None
            if vec:
                base = _cosine(qvec, vec)
            else:
                base = 0.15
            # 稳定排序：同分时保持 DB 权重顺序（倒序枚举配合 reverse）
            scored.append((base + kw_bonus(record), -index, record))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored]
    except Exception:
        _embed_fail_until = time.time() + _EMBED_BACKOFF_SECONDS
        return None


def semantic_config(config: dict) -> dict | None:
    """全局配置 → 语义检索参数；禁用或无可用端点时返回 None。

    endpoint/api_key 留空回退顶层对话配置（OpenAI 兼容端点约定）。
    """
    mem_cfg = config.get("memory") if isinstance(config.get("memory"), dict) else {}
    cfg = {**SEMANTIC_DEFAULTS, **(mem_cfg.get("semantic") if isinstance(mem_cfg.get("semantic"), dict) else {})}
    if not cfg.get("enabled", True):
        return None
    endpoint = str(cfg.get("endpoint") or config.get("endpoint") or "").strip()
    if not endpoint:
        return None
    return {
        "endpoint": endpoint.rstrip("/"),
        "api_key": str(cfg.get("api_key") or config.get("api_key") or ""),
        "model": str(cfg.get("model") or SEMANTIC_DEFAULTS["model"]),
    }


def embed_texts(texts: list[str], *, endpoint: str, api_key: str, model: str) -> list[list[float]]:
    """OpenAI 兼容 /embeddings 批量向量化（带进程内缓存）。失败抛异常由 caller 兜底。"""
    results: list[list[float]] = []
    pending: list[int] = []
    keys = [(model, _vec_key(t)) for t in texts]
    for i, key in enumerate(keys):
        cached = _VEC_CACHE.get(key)
        if cached is not None:
            results.append(cached)
        else:
            results.append([])
            pending.append(i)
    if pending:
        resp = httpx.post(
            f"{endpoint.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            json={"model": model, "input": [texts[i] for i in pending]},
            timeout=15,
        )
        resp.raise_for_status()
        data = sorted(resp.json().get("data", []), key=lambda item: int(item.get("index", 0)))
        for slot, item in zip(pending, data):
            vec = [float(x) for x in (item.get("embedding") or [])]
            if vec:
                _VEC_CACHE[keys[slot]] = vec
                results[slot] = vec
    return results


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = norm_a = norm_b = 0.0
    for i in range(n):
        dot += a[i] * b[i]
        norm_a += a[i] * a[i]
        norm_b += b[i] * b[i]
    denom = (norm_a ** 0.5) * (norm_b ** 0.5)
    return dot / denom if denom > 1e-9 else 0.0


def _vec_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    keys = set(row.keys())
    return MemoryRecord(
        id=int(row["id"]),
        kind=str(row["kind"]),
        content=str(row["content"]),
        source=str(row["source"]),
        scope=str(row["scope"]),
        weight=float(row["weight"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        embedding=bytes(row["embedding"]) if "embedding" in keys and row["embedding"] is not None else None,
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
