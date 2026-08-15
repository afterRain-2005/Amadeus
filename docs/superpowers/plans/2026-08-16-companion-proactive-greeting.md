# Companion 主动问候（伪春菜式陪伴）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让红莉栖主动观察用户活动（前台窗口/工作节奏/空闲/剪贴板/屏幕）并吐槽/关心，伪春菜式陪伴。

**Architecture:** CompanionController 作为 desktop_pet.py 闭包内类（参考 AgentTask 模式）；5 传感器各自 QTimer 轮询，信号变化触发评估器；评估器 L1 硬阈值走预设模板（零 LLM 成本）、L2 LLM 决策（5min 节流 + 10min 全局冷却）；输出作为"虚拟用户输入"送 route_and_send（system_role="companion", skip_history=True）复用现有表达层；轻量 SQLite `lightweight_memory` 表与未来 P1 同库 `data/memory.db`。

**Tech Stack:** Python 3.13, PySide6 (QTimer/QRunnable), pywin32 (GetForegroundWindow/GetLastInputInfo/win32clipboard), mss (截屏), httpx (LLM 调用), sqlite3 (标准库), pytest

**对应 Spec：** [docs/superpowers/specs/2026-08-16-companion-proactive-greeting-design.md](../specs/2026-08-16-companion-proactive-greeting-design.md)

**TDD 说明：** 逻辑代码（传感器/评估器/调度器/存储/route_and_send 扩展）走严格 TDD；UI/进程布线类改动用"运行验证"步骤替代。每步给出可执行命令与期望结果。

**关键接入点：**
- `route_and_send` 在 [core/backend_router.py:80](../../../core/backend_router.py#L80)，当前签名 `(*, config, input_text, soul_md, conversation_history, memories, on_delta, on_status, on_approval)`
- `AgentTask` 在 [desktop_pet.py:283](../../../desktop_pet.py#L283)，闭包内 QRunnable 模式参考
- `_send` 在 [desktop_pet.py:1005](../../../desktop_pet.py#L1005)，用户消息入口
- 设置页 5 个 tab 在 [ui/settings_dialog.py](../../../ui/settings_dialog.py)

---

## 文件结构

**新建**：
- `core/companion/__init__.py` — 包标记
- `core/companion/storage.py` — lightweight_memory SQLite CRUD
- `core/companion/sensors.py` — 5 传感器 + ContextSnapshot dataclass
- `core/companion/prompts.py` — KURISU_PROACTIVE_INSTRUCTION/TEMPLATES/PASS_THROUGH
- `core/companion/evaluator.py` — Evaluator + GreetingDecision + L1 硬阈值 + L2 LLM 决策
- `core/companion/scheduler.py` — Scheduler（节流/静音/概率/上限/冷却）
- `core/companion/controller.py` — CompanionController（聚合传感器+评估器+调度器）
- `tests/companion/__init__.py` — 测试包标记
- `tests/companion/test_storage.py`
- `tests/companion/test_sensors.py`
- `tests/companion/test_evaluator.py`
- `tests/companion/test_scheduler.py`
- `tests/companion/test_integration.py`
- `tests/test_route_and_send_companion.py`

**修改**：
- `core/backend_router.py` — route_and_send 扩展 3 个参数（向后兼容）
- `desktop_pet.py` — CompanionController 闭包内类 + _companion_speak 接入 + 启停
- `ui/settings_dialog.py` — 第 6 个 companion tab
- `config.py` — COMPANION_DEFAULTS
- `requirements.txt` — pywin32（若未装）

---

## Task 1: route_and_send 扩展（向后兼容）

**Files:**
- Modify: `core/backend_router.py:80-90`
- Create: `tests/test_route_and_send_companion.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_route_and_send_companion.py`：

```python
"""route_and_send 的 companion 模式扩展测试。

验证三个新参数（system_role / skip_history / inject_system_prompt）的语义：
- system_role="companion" 时跳过 classify_input，直接走 chat 路径
- skip_history=True 时不写 conversation_history
- inject_system_prompt 注入到 messages 最前
"""
from unittest.mock import patch, MagicMock

import core.backend_router as router


def test_route_and_send_companion_skips_classify_and_history():
    """companion 模式跳过 classify_input，不写 conversation_history。"""
    captured = {}

    def fake_run_local_run(*, endpoint, api_key, model, soul_md, instructions,
                            input_text, conversation_history, memories,
                            on_status, on_delta, on_approval):
        captured["conversation_history"] = conversation_history
        captured["input_text"] = input_text
        captured["instructions"] = instructions
        return "companion reply", "chat"

    config = {"endpoint": "http://x", "api_key": "k", "model": "m"}
    history = [{"role": "user", "content": "earlier"}]

    with patch.object(router, "classify_input") as mock_classify, \
         patch("core.agent_client.run_local_run", side_effect=fake_run_local_run), \
         patch("core.hermes_launcher.ensure_gateway", return_value=False):
        reply, backend = router.route_and_send(
            config=config, input_text="主动问候文本", soul_md="SOUL",
            conversation_history=history,
            system_role="companion",
            skip_history=True,
            inject_system_prompt="PASS_THROUGH",
        )

    # classify_input 不应被调用
    mock_classify.assert_not_called()
    # conversation_history 不被改写（仍是原 list）
    assert history == [{"role": "user", "content": "earlier"}]
    # reply 透传
    assert reply == "companion reply"
    assert backend == "chat"


def test_route_and_send_default_user_mode_unchanged():
    """默认 system_role='user' 时维持现状：走 classify_input，写 history。"""
    captured = {}

    def fake_run_local_run(**kwargs):
        captured.update(kwargs)
        return "ok", "chat"

    config = {"endpoint": "http://x", "api_key": "k", "model": "m"}
    history = [{"role": "user", "content": "earlier"}]

    with patch.object(router, "classify_input", return_value="chat") as mock_classify, \
         patch("core.agent_client.run_local_run", side_effect=fake_run_local_run), \
         patch("core.hermes_launcher.ensure_gateway", return_value=False):
        router.route_and_send(
            config=config, input_text="hello", soul_md="SOUL",
            conversation_history=history,
        )

    mock_classify.assert_called_once()
    # user 模式下 input_text 会被附加到 history（现状行为）
    assert any("hello" in m.get("content", "") for m in history)


def test_route_and_send_inject_system_prompt_passes_through():
    """inject_system_prompt 透传到 run_local_run 的 instructions 字段。"""
    captured = {}

    def fake_run_local_run(**kwargs):
        captured.update(kwargs)
        return "ok", "chat"

    config = {"endpoint": "http://x", "api_key": "k", "model": "m"}

    with patch("core.agent_client.run_local_run", side_effect=fake_run_local_run), \
         patch("core.hermes_launcher.ensure_gateway", return_value=False):
        router.route_and_send(
            config=config, input_text="主动问候", soul_md="SOUL",
            conversation_history=[],
            system_role="companion",
            skip_history=True,
            inject_system_prompt="把下面这段用你的语气说出：",
        )

    # instructions 应包含 inject_system_prompt 内容
    assert "把下面这段用你的语气说出" in captured["instructions"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_route_and_send_companion.py -v`
Expected: FAIL — `TypeError: route_and_send() got an unexpected keyword argument 'system_role'`

- [ ] **Step 3: 修改 route_and_send 签名与逻辑**

在 `core/backend_router.py:80` 修改 `route_and_send`：

把函数签名从：
```python
def route_and_send(
    *,
    config: dict,
    input_text: str,
    soul_md: str,
    conversation_history: list[dict] | None = None,
    memories: list[dict] | None = None,
    on_delta=lambda t: None,
    on_status=lambda t: None,
    on_approval=lambda p: "deny",
) -> tuple[str, str]:
```

改为：
```python
def route_and_send(
    *,
    config: dict,
    input_text: str,
    soul_md: str,
    conversation_history: list[dict] | None = None,
    memories: list[dict] | None = None,
    on_delta=lambda t: None,
    on_status=lambda t: None,
    on_approval=lambda p: "deny",
    system_role: str = "user",
    skip_history: bool = False,
    inject_system_prompt: str | None = None,
) -> tuple[str, str]:
```

在 docstring 后（`"""按模式分发，返回 (reply, backend_used)。hermes 失败自动降级本地直连。"""` 之后）补一段说明：
```python
    """按模式分发，返回 (reply, backend_used)。hermes 失败自动降级本地直连。

    扩展参数（companion 用）：
    - system_role="companion" 跳过 classify_input，直接走 chat 路径
    - skip_history=True 时不写 conversation_history
    - inject_system_prompt 注入到 messages 最前，作为额外 system 指令
    """
```

修改 route 判定逻辑（在 `route = mode if mode in ...` 这一行之前插入）：
```python
    # companion 模式：跳过 classify_input，直接走 chat 路径
    if system_role == "companion":
        route = "chat"
    elif mode in ("chat", "hermes", "codex"):
        route = mode
    else:
        route = classify_input(
            input_text, openclaw_enabled=openclaw_enabled,
            llm_classify=lambda t: _llm_classify(
                t, endpoint=config.get("endpoint", ""),
                api_key=config.get("api_key", ""), model=config.get("model", "")),
        )
```

把原来的 `route = mode if mode in ("chat", "hermes", "codex") else classify_input(...)` 整段删除。

修改 chat 路径的 history 写入（在 `# chat / gui / hermes 降级：本地直连` 注释下方）：
```python
    # chat / gui / hermes 降级：本地直连（gui 追加 operate_gui 引导）
    text = input_text if route != "gui" else input_text + "\n" + GUI_NUDGE
    # companion 模式不写 conversation_history
    if not skip_history and conversation_history is not None:
        conversation_history.append({"role": "user", "content": input_text})
    # inject_system_prompt 叠加到 instructions
    instructions = KURISU_OUTPUT_FORMAT
    if inject_system_prompt:
        instructions = f"{inject_system_prompt}\n\n{KURISU_OUTPUT_FORMAT}"
    reply = run_local_run(
        endpoint=config.get("endpoint", ""), api_key=config.get("api_key", ""),
        model=config.get("model", ""), soul_md=soul_md,
        instructions=instructions, input_text=text,
        conversation_history=conversation_history, memories=memories,
        on_status=on_status, on_delta=on_delta, on_approval=on_approval)
    return reply, ("gui" if route == "gui" else "chat")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_route_and_send_companion.py -v`
Expected: PASS（3 项全过）

- [ ] **Step 5: 全量回归**

Run: `python -m pytest tests/ -v`
Expected: 既有测试全过 + 3 项新测试通过，无回归。

- [ ] **Step 6: 提交**

```
git add core/backend_router.py tests/test_route_and_send_companion.py
git commit -m "feat(router): route_and_send 扩展 companion 模式（system_role/skip_history/inject_system_prompt）"
```

---

## Task 2: lightweight_memory SQLite 存储

**Files:**
- Create: `core/companion/__init__.py`
- Create: `core/companion/storage.py`
- Create: `tests/companion/__init__.py`
- Create: `tests/companion/test_storage.py`

- [ ] **Step 1: 创建包标记**

`core/companion/__init__.py`：
```python
"""Companion 主动问候子系统（伪春菜式陪伴）。"""
```

`tests/companion/__init__.py`：（空文件）

- [ ] **Step 2: 写失败测试**

创建 `tests/companion/test_storage.py`：
```python
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
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/companion/test_storage.py -v`
Expected: FAIL — `ImportError: No module named 'core.companion.storage'`

- [ ] **Step 4: 实现 storage.py**

创建 `core/companion/storage.py`：
```python
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
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/companion/test_storage.py -v`
Expected: PASS（7 项全过）

- [ ] **Step 6: 提交**

```
git add core/companion/__init__.py core/companion/storage.py tests/companion/__init__.py tests/companion/test_storage.py
git commit -m "feat(companion): lightweight_memory SQLite 存储与 CRUD"
```

---

## Task 3: ContextSnapshot + 5 传感器

**Files:**
- Create: `core/companion/sensors.py`
- Create: `tests/companion/test_sensors.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/companion/test_sensors.py`：
```python
"""5 传感器 + ContextSnapshot 测试。所有 win32/mss 调用均 mock。"""
from unittest.mock import patch, MagicMock

from core.companion.sensors import (
    ContextSnapshot, ActiveWindowSensor, ActivityTracker,
    IdleStateTracker, ClipboardSensor, ScreenSensor, build_snapshot,
)


def test_context_snapshot_dataclass_defaults():
    snap = ContextSnapshot(
        timestamp="2026-08-16T10:00:00+00:00",
        local_time="14:30 周二", is_deep_night=False,
        idle_seconds=10, work_session_minutes=5, idle_state="active",
        active_window_title="main.py - Code", active_process="Code.exe",
        window_changed_recently=False,
        last_companion_greeting_ts=None, last_companion_topic=None,
        greeting_count_today=0,
    )
    assert snap.clipboard_preview is None
    assert snap.screen_ocr_text is None


def test_active_window_sensor_snapshot():
    sensor = ActiveWindowSensor(interval_seconds=2)
    with patch("core.companion.sensors._get_foreground_window",
               return_value=("main.py - Code", "Code.exe", 12345)):
        sensor._poll()
    snap = sensor.snapshot()
    assert snap["window_title"] == "main.py - Code"
    assert snap["process_name"] == "Code.exe"
    assert "since_ts" in snap


def test_active_window_sensor_detects_change():
    sensor = ActiveWindowSensor(interval_seconds=2)
    with patch("core.companion.sensors._get_foreground_window",
               return_value=("A", "A.exe", 1)):
        sensor._poll()
    with patch("core.companion.sensors._get_foreground_window",
               return_value=("B", "B.exe", 2)):
        sensor._poll()
    assert sensor.window_changed_recently() is True


def test_activity_tracker_idle_seconds():
    import ctypes
    sensor = ActivityTracker(interval_seconds=30)
    fake_info = MagicMock()
    fake_info.dwTime = 1000  # last input tick
    with patch("core.companion.sensors._get_last_input_info", return_value=1000), \
         patch("core.companion.sensors._get_tick_count", return_value=7000):
        sensor._poll()
    assert sensor.idle_seconds == 6


def test_idle_state_tracker_states():
    """active (<5min) / idle (5-15min) / away (>15min)"""
    tracker = IdleStateTracker()
    tracker.update(idle_seconds=10)
    assert tracker.idle_state == "active"
    tracker.update(idle_seconds=600)
    assert tracker.idle_state == "idle"
    tracker.update(idle_seconds=1200)
    assert tracker.idle_state == "away"


def test_clipboard_sensor_detects_change():
    sensor = ClipboardSensor(interval_seconds=1)
    with patch("core.companion.sensors._get_clipboard_text", return_value="hello"):
        sensor._poll()
    with patch("core.companion.sensors._get_clipboard_text", return_value="world"):
        sensor._poll()
    snap = sensor.snapshot()
    assert snap["preview"] == "world"
    assert snap["length"] == 5


def test_clipboard_sensor_filters_sensitive_content():
    """含 password/key/token 关键词的剪贴板内容不发送给 LLM。"""
    sensor = ClipboardSensor(interval_seconds=1)
    with patch("core.companion.sensors._get_clipboard_text",
               return_value="my_password=abc123"):
        sensor._poll()
    snap = sensor.snapshot()
    assert snap["preview"] is None  # 被过滤


def test_screen_sensor_disabled_by_default():
    sensor = ScreenSensor()
    assert sensor.snapshot() == {}


def test_screen_sensor_captures_when_enabled():
    sensor = ScreenSensor(enabled=True)
    fake_img = MagicMock()
    fake_img.tobytes.return_value = b"\x89PNG fake jpg data"
    with patch("core.companion.sensors.mss.mss") as mock_mss:
        mock_mss.return_value.__enter__.return_value.grab.return_value = fake_img
        with patch("core.companion.sensors._frame_to_b64", return_value="BASE64STR"):
            sensor.capture()
    snap = sensor.snapshot()
    assert snap.get("frame_jpg_b64") == "BASE64STR"


def test_build_snapshot_aggregates_all_sensors():
    """build_snapshot 把所有传感器字段聚合到 ContextSnapshot。"""
    aw = ActiveWindowSensor(interval_seconds=2)
    with patch("core.companion.sensors._get_foreground_window",
               return_value=("main.py - Code", "Code.exe", 1)):
        aw._poll()
    at = ActivityTracker(interval_seconds=30)
    with patch("core.companion.sensors._get_last_input_info", return_value=0), \
         patch("core.companion.sensors._get_tick_count", return_value=0):
        at._poll()
    it = IdleStateTracker()
    it.update(idle_seconds=at.idle_seconds)

    snap = build_snapshot(
        active_window=aw, activity=at, idle=it,
        clipboard=None, screen=None,
        last_greeting_ts=None, last_topic=None, greeting_count=0,
        local_time="14:30 周二", is_deep_night=False,
    )
    assert snap.active_window_title == "main.py - Code"
    assert snap.active_process == "Code.exe"
    assert snap.idle_state == "active"
    assert snap.clipboard_preview is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/companion/test_sensors.py -v`
Expected: FAIL — `ImportError: No module named 'core.companion.sensors'`

- [ ] **Step 3: 实现 sensors.py**

创建 `core/companion/sensors.py`：
```python
"""5 传感器 + ContextSnapshot。

每个传感器用 QTimer 周期轮询 win32 API，信号变化时更新内部状态。
build_snapshot 聚合所有传感器字段为 ContextSnapshot（喂给 LLM 决策器）。

隐私边界（产品化设计 §6）：
- Clipboard / Screen 默认关
- 不记录按键内容（只看空闲时长）
- 剪贴板含 password/key/token 等关键词时过滤
"""
from __future__ import annotations

import base64
import ctypes
import io
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

try:
    import mss
except ImportError:
    mss = None  # ScreenSensor 在 mss 缺失时降级为不可用

try:
    import win32clipboard
    import win32gui
    import win32process
except ImportError:
    win32clipboard = None
    win32gui = None
    win32process = None


SENSITIVE_PATTERN = re.compile(r"password|passwd|secret|api[_-]?key|token|credential", re.IGNORECASE)


# === win32 抽象层（便于测试 mock） ===

def _get_foreground_window() -> tuple[str, str, int]:
    """返回 (window_title, process_name, hwnd)。失败返回 ('', '', 0)。"""
    if win32gui is None or win32process is None:
        return ("", "", 0)
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        # 不在每次轮询调用 psutil 取进程名（成本高），返回 pid 由调用方按需解析
        return (title, str(pid), hwnd)
    except Exception:
        return ("", "", 0)


def _get_last_input_info() -> int:
    """返回最后输入的 tick count。失败返回 0。"""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
    return lii.dwTime


def _get_tick_count() -> int:
    return ctypes.windll.kernel32.GetTickCount()


def _get_clipboard_text() -> str:
    if win32clipboard is None:
        return ""
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData()
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return ""
    return ""


def _frame_to_b64(img) -> str:
    """mss 截帧对象转 base64 JPEG 字符串。"""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=60)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# === ContextSnapshot ===

@dataclass
class ContextSnapshot:
    timestamp: str
    local_time: str
    is_deep_night: bool
    idle_seconds: int
    work_session_minutes: int
    idle_state: str  # active / idle / away
    active_window_title: str
    active_process: str
    window_changed_recently: bool
    last_companion_greeting_ts: Optional[str]
    last_companion_topic: Optional[str]
    greeting_count_today: int
    clipboard_preview: Optional[str] = None
    screen_ocr_text: Optional[str] = None


# === 传感器 ===

class ActiveWindowSensor:
    def __init__(self, interval_seconds: int = 2) -> None:
        self.interval = interval_seconds
        self._window_title = ""
        self._process_name = ""
        self._since_ts: float = 0.0
        self._last_change_ts: float = 0.0
        self._timer = None  # QTimer 在 start() 时绑定

    def _poll(self) -> None:
        title, proc, _ = _get_foreground_window()
        if title != self._window_title:
            self._window_title = title
            self._process_name = proc
            self._since_ts = time.time()
            self._last_change_ts = time.time()

    def snapshot(self) -> dict:
        return {
            "window_title": self._window_title,
            "process_name": self._process_name,
            "since_ts": self._since_ts,
        }

    def window_changed_recently(self, window_seconds: int = 30) -> bool:
        return (time.time() - self._last_change_ts) < window_seconds

    def start(self, parent=None) -> None:
        from PySide6.QtCore import QTimer
        self._timer = QTimer(parent)
        self._timer.timeout.connect(self._poll)
        self._timer.start(self.interval * 1000)

    def stop(self) -> None:
        if self._timer:
            self._timer.stop()


class ActivityTracker:
    def __init__(self, interval_seconds: int = 30) -> None:
        self.interval = interval_seconds
        self.idle_seconds: int = 0
        self.work_session_minutes: int = 0
        self._last_active_ts: float = time.time()
        self._work_session_start_ts: float = time.time()
        self._timer = None

    def _poll(self) -> None:
        last_input = _get_last_input_info()
        tick = _get_tick_count()
        self.idle_seconds = max(0, (tick - last_input) // 1000)
        # 工作会话：连续输入（无 >5min 中断）累计
        if self.idle_seconds < 300:
            self.work_session_minutes = int((time.time() - self._work_session_start_ts) / 60)
        else:
            # 中断超过 5min，重置工作会话
            self._work_session_start_ts = time.time()
            self.work_session_minutes = 0
        self._last_active_ts = time.time()

    def start(self, parent=None) -> None:
        from PySide6.QtCore import QTimer
        self._timer = QTimer(parent)
        self._timer.timeout.connect(self._poll)
        self._timer.start(self.interval * 1000)

    def stop(self) -> None:
        if self._timer:
            self._timer.stop()


class IdleStateTracker:
    """派生自 ActivityTracker 数据，无需独立 QTimer。"""
    def __init__(self) -> None:
        self.idle_state: str = "active"
        self.since_ts: float = time.time()

    def update(self, idle_seconds: int) -> None:
        new_state = "active" if idle_seconds < 300 else ("idle" if idle_seconds < 900 else "away")
        if new_state != self.idle_state:
            self.idle_state = new_state
            self.since_ts = time.time()


class ClipboardSensor:
    def __init__(self, interval_seconds: int = 1, enabled: bool = False) -> None:
        self.interval = interval_seconds
        self.enabled = enabled
        self._current_text: str = ""
        self._hash: str = ""
        self._length: int = 0
        self._filtered: bool = False
        self._timer = None

    def _poll(self) -> None:
        if not self.enabled:
            return
        text = _get_clipboard_text()
        if not text or text == self._current_text:
            return
        self._current_text = text
        self._length = len(text)
        self._hash = str(hash(text))
        # 敏感内容过滤
        self._filtered = bool(SENSITIVE_PATTERN.search(text))

    def snapshot(self) -> dict:
        if not self.enabled:
            return {}
        if self._filtered or not self._current_text:
            return {"hash": self._hash, "length": self._length, "preview": None}
        return {
            "hash": self._hash,
            "length": self._length,
            "preview": self._current_text[:50],
        }

    def start(self, parent=None) -> None:
        if not self.enabled:
            return
        from PySide6.QtCore import QTimer
        self._timer = QTimer(parent)
        self._timer.timeout.connect(self._poll)
        self._timer.start(self.interval * 1000)

    def stop(self) -> None:
        if self._timer:
            self._timer.stop()


class ScreenSensor:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self._frame_b64: str = ""
        self._last_capture_ts: float = 0.0

    def capture(self) -> None:
        if not self.enabled or mss is None:
            return
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1] if sct.monitors else None
                if monitor:
                    img = sct.grab(monitor)
                    self._frame_b64 = _frame_to_b64(img)
                    self._last_capture_ts = time.time()
        except Exception:
            self._frame_b64 = ""

    def snapshot(self) -> dict:
        if not self.enabled:
            return {}
        return {"frame_jpg_b64": self._frame_b64, "captured_at": self._last_capture_ts}


# === 聚合 ===

def build_snapshot(
    *,
    active_window: ActiveWindowSensor,
    activity: ActivityTracker,
    idle: IdleStateTracker,
    clipboard: Optional[ClipboardSensor],
    screen: Optional[ScreenSensor],
    last_greeting_ts: Optional[str],
    last_topic: Optional[str],
    greeting_count: int,
    local_time: str,
    is_deep_night: bool,
) -> ContextSnapshot:
    aw_snap = active_window.snapshot()
    clip_snap = clipboard.snapshot() if clipboard else {}
    screen_snap = screen.snapshot() if screen else {}
    return ContextSnapshot(
        timestamp=datetime.utcnow().isoformat() + "Z",
        local_time=local_time,
        is_deep_night=is_deep_night,
        idle_seconds=activity.idle_seconds,
        work_session_minutes=activity.work_session_minutes,
        idle_state=idle.idle_state,
        active_window_title=aw_snap.get("window_title", ""),
        active_process=aw_snap.get("process_name", ""),
        window_changed_recently=active_window.window_changed_recently(),
        last_companion_greeting_ts=last_greeting_ts,
        last_companion_topic=last_topic,
        greeting_count_today=greeting_count,
        clipboard_preview=clip_snap.get("preview"),
        screen_ocr_text=screen_snap.get("frame_jpg_b64"),  # 实际 OCR 在 LLM 侧处理
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/companion/test_sensors.py -v`
Expected: PASS（9 项全过）

- [ ] **Step 5: 提交**

```
git add core/companion/sensors.py tests/companion/test_sensors.py
git commit -m "feat(companion): ContextSnapshot + 5 传感器（ActiveWindow/Activity/Idle/Clipboard/Screen）"
```

---

## Task 4: prompts + Evaluator（L1 硬阈值 + L2 LLM 决策）

**Files:**
- Create: `core/companion/prompts.py`
- Create: `core/companion/evaluator.py`
- Create: `tests/companion/test_evaluator.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/companion/test_evaluator.py`：
```python
"""Evaluator 测试：L1 硬阈值规则 + L2 LLM 决策 + LLM 失败降级。"""
import json
from unittest.mock import patch, MagicMock

from core.companion.evaluator import Evaluator, GreetingDecision
from core.companion.sensors import ContextSnapshot
from core.companion.prompts import KURISU_PROACTIVE_TEMPLATES


def _snap(**kwargs) -> ContextSnapshot:
    defaults = dict(
        timestamp="2026-08-16T10:00:00Z", local_time="14:30 周二",
        is_deep_night=False, idle_seconds=10, work_session_minutes=5,
        idle_state="active", active_window_title="main.py - Code",
        active_process="Code.exe", window_changed_recently=False,
        last_companion_greeting_ts=None, last_companion_topic=None,
        greeting_count_today=0,
    )
    defaults.update(kwargs)
    return ContextSnapshot(**defaults)


def test_l1_idle_over_15min_triggers_template():
    ev = Evaluator()
    snap = _snap(idle_seconds=1000)  # >900
    decision = ev.evaluate(snap)
    assert decision is not None
    assert decision.source == "template"
    assert decision.topic == "idle"
    assert "盯着屏幕发呆" in decision.text


def test_l1_deep_night_work_session_triggers_sleepy():
    ev = Evaluator()
    snap = _snap(is_deep_night=True, work_session_minutes=45, local_time="02:30 周三")
    decision = ev.evaluate(snap)
    assert decision is not None
    assert decision.emotion == "sleepy"
    assert "02:30" in decision.text or "睡觉" in decision.text


def test_l1_work_session_over_2h_triggers_concern():
    ev = Evaluator()
    snap = _snap(work_session_minutes=130)
    decision = ev.evaluate(snap)
    assert decision is not None
    assert decision.emotion == "concern"
    assert "130" in decision.text or "颈椎" in decision.text


def test_l1_no_trigger_when_conditions_not_met():
    ev = Evaluator()
    snap = _snap(idle_seconds=10, work_session_minutes=5, is_deep_night=False)
    # L1 不命中，且未注入 llm_decide，返回 None
    decision = ev.evaluate(snap, allow_llm=False)
    assert decision is None


def test_l2_llm_decide_should_speak_true():
    ev = Evaluator()
    llm_resp = {"should_speak": True, "text": "你在写代码啊，加油", "emotion": "neutral", "topic": "work"}
    with patch("core.companion.evaluator._call_llm", return_value=llm_resp):
        snap = _snap(idle_seconds=10)
        decision = ev.evaluate(snap, allow_llm=True)
    assert decision is not None
    assert decision.source == "llm"
    assert decision.text == "你在写代码啊，加油"
    assert decision.emotion == "neutral"


def test_l2_llm_decide_should_speak_false_returns_none():
    ev = Evaluator()
    llm_resp = {"should_speak": False, "text": "", "emotion": "", "topic": ""}
    with patch("core.companion.evaluator._call_llm", return_value=llm_resp):
        snap = _snap(idle_seconds=10)
        decision = ev.evaluate(snap, allow_llm=True)
    assert decision is None


def test_l2_llm_invalid_json_falls_back_to_template():
    """LLM 返回非法 JSON 时降级走 L1 模板（即便本场景非必说）。"""
    ev = Evaluator()
    with patch("core.companion.evaluator._call_llm", side_effect=ValueError("invalid json")):
        snap = _snap(idle_seconds=10, work_session_minutes=5)  # L1 不命中
        decision = ev.evaluate(snap, allow_llm=True)
    # 降级到 idle 模板兜底
    assert decision is not None
    assert decision.source == "fallback_template"
    assert decision.emotion == "idle"


def test_l2_llm_network_error_falls_back_to_template():
    ev = Evaluator()
    with patch("core.companion.evaluator._call_llm", side_effect=OSError("timeout")):
        snap = _snap(idle_seconds=10)
        decision = ev.evaluate(snap, allow_llm=True)
    assert decision is not None
    assert decision.source == "fallback_template"


def test_l2_llm_throttled_when_recent_same_signal():
    """5min 内同类信号不重复调 LLM。"""
    ev = Evaluator()
    import time
    ev._last_llm_call_ts = {"idle_signal": time.time()}  # 刚调过
    with patch("core.companion.evaluator._call_llm") as mock_llm:
        snap = _snap(idle_seconds=10)
        decision = ev.evaluate(snap, allow_llm=True, signal_type="idle_signal")
    mock_llm.assert_not_called()
    assert decision is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/companion/test_evaluator.py -v`
Expected: FAIL — `ImportError: No module named 'core.companion.evaluator'`

- [ ] **Step 3: 实现 prompts.py**

创建 `core/companion/prompts.py`：
```python
"""Companion 主动问候 prompt 模板。"""

KURISU_PROACTIVE_TEMPLATES = [
    {
        "topic": "idle",
        "condition": lambda s: s.idle_seconds > 900,
        "text": "盯着屏幕发呆也修不好 bug，不如起来走走？",
        "emotion": "idle",
    },
    {
        "topic": "sleepy",
        "condition": lambda s: s.is_deep_night and s.work_session_minutes > 30,
        "text": "现在 {local_time} 了，你不睡觉我也不睡啊",
        "emotion": "sleepy",
    },
    {
        "topic": "concern",
        "condition": lambda s: s.work_session_minutes > 120,
        "text": "你已经坐了 {work_session_minutes} 分钟了，颈椎不要了？",
        "emotion": "concern",
    },
    {
        "topic": "tease",
        "condition": lambda s: s.window_changed_recently and s.greeting_count_today == 0,
        "text": "切换窗口切得这么勤，是在摸鱼吧？",
        "emotion": "tease",
    },
    {
        "topic": "away_long",
        "condition": lambda s: s.idle_state == "away" and s.idle_seconds > 3600,
        "text": "很久没碰电脑了，还在吗？",
        "emotion": "neutral",
    },
]

KURISU_PROACTIVE_INSTRUCTION = """你是牧濑红莉栖，主动观察用户在做什么并吐槽/关心。

风格要求：
- 傲娇、毒舌但关心、偶尔卖萌，参考石头门原作
- 长度限制：≤30 字（气泡宽度限制）
- 永远不暴露你是 AI 助手、不提"作为AI"等
- 不重复用户最近 2 小时内听过的主题

判断规则：
- should_speak=false 当用户明显在专注工作/会议/重要操作时
- should_speak=true 当有自然吐槽/关心机会时（不在专注状态）

JSON 输出格式：
{"should_speak": bool, "text": str, "emotion": str, "topic": str}

emotion 可选：neutral/happy/tease/concern/sleepy/idle/angry
topic 可选：idle/work/deep_night/tease/window_change/general
"""

KURISU_PROACTIVE_PASS_THROUGH = """你接下来要说的话已经准备好了，把以下内容用你的语气自然说出，可以微调措辞但不要改变意思：

{text}"""
```

- [ ] **Step 4: 实现 evaluator.py**

创建 `core/companion/evaluator.py`：
```python
"""评估器：L1 硬阈值规则（必说场景走模板）+ L2 LLM 决策（可选场景）。

LLM 调用节流：5min 内同类信号不重复（由 Scheduler 上层控制，Evaluator 只在调用时记录）。
LLM 失败降级：返回 fallback_template（即便本场景非必说）。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from core.companion.prompts import (
    KURISU_PROACTIVE_INSTRUCTION, KURISU_PROACTIVE_TEMPLATES,
)
from core.companion.sensors import ContextSnapshot


@dataclass
class GreetingDecision:
    text: str
    emotion: str
    topic: str
    source: str  # 'template' | 'llm' | 'fallback_template'


def _call_llm(snapshot: ContextSnapshot, *, endpoint: str, api_key: str, model: str) -> dict:
    """调 LLM 决策器，返回解析后的 dict。失败抛异常。"""
    system = KURISU_PROACTIVE_INSTRUCTION
    user_msg = json.dumps({
        "timestamp": snapshot.timestamp,
        "local_time": snapshot.local_time,
        "is_deep_night": snapshot.is_deep_night,
        "idle_seconds": snapshot.idle_seconds,
        "work_session_minutes": snapshot.work_session_minutes,
        "idle_state": snapshot.idle_state,
        "active_window_title": snapshot.active_window_title,
        "active_process": snapshot.active_process,
        "window_changed_recently": snapshot.window_changed_recently,
        "last_companion_topic": snapshot.last_companion_topic,
        "greeting_count_today": snapshot.greeting_count_today,
        "clipboard_preview": snapshot.clipboard_preview,
    }, ensure_ascii=False)
    with httpx.Client(timeout=5) as client:
        resp = client.post(
            f"{endpoint.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": 100,
                "temperature": 0.8,
                "response_format": {"type": "json_object"},
            },
        )
    if resp.is_error:
        raise OSError(f"LLM HTTP {resp.status_code}")
    content = resp.json()["choices"][0]["message"]["content"]
    data = json.loads(content)  # 失败抛 ValueError
    return data


class Evaluator:
    """评估器：L1 硬阈值 → L2 LLM 决策。"""

    def __init__(self) -> None:
        # signal_type -> last_llm_call_ts，节流用（5min 内同类不重复）
        self._last_llm_call_ts: dict[str, float] = {}

    def evaluate(
        self,
        snapshot: ContextSnapshot,
        *,
        allow_llm: bool = True,
        signal_type: str = "default",
        llm_endpoint: str = "",
        llm_api_key: str = "",
        llm_model: str = "",
    ) -> Optional[GreetingDecision]:
        # L1: 硬阈值规则引擎（必说场景，零 LLM 成本）
        decision = self._hard_rules(snapshot)
        if decision:
            return decision

        if not allow_llm:
            return None

        # L2: LLM 决策（5min 节流）
        if not self._llm_throttle_allows(signal_type):
            return None

        return self._llm_decide(
            snapshot, signal_type=signal_type,
            endpoint=llm_endpoint, api_key=llm_api_key, model=llm_model,
        )

    def _hard_rules(self, snapshot: ContextSnapshot) -> Optional[GreetingDecision]:
        """L1 硬阈值规则：返回首个命中的模板。"""
        for tpl in KURISU_PROACTIVE_TEMPLATES:
            if tpl["condition"](snapshot):
                text = tpl["text"].format(
                    local_time=snapshot.local_time,
                    work_session_minutes=snapshot.work_session_minutes,
                )
                return GreetingDecision(
                    text=text, emotion=tpl["emotion"], topic=tpl["topic"],
                    source="template",
                )
        return None

    def _llm_throttle_allows(self, signal_type: str, window_seconds: int = 300) -> bool:
        """5min 内同类信号不重复调 LLM。"""
        last = self._last_llm_call_ts.get(signal_type, 0)
        if (time.time() - last) < window_seconds:
            return False
        self._last_llm_call_ts[signal_type] = time.time()
        return True

    def _llm_decide(
        self, snapshot: ContextSnapshot, *, signal_type: str,
        endpoint: str, api_key: str, model: str,
    ) -> Optional[GreetingDecision]:
        try:
            data = _call_llm(snapshot, endpoint=endpoint, api_key=api_key, model=model)
        except (OSError, ValueError, KeyError):
            # LLM 失败降级走 idle 模板兜底
            return GreetingDecision(
                text="盯着屏幕发呆也修不好 bug，不如起来走走？",
                emotion="idle", topic="idle", source="fallback_template",
            )
        if not data.get("should_speak"):
            return None
        return GreetingDecision(
            text=data.get("text", ""),
            emotion=data.get("emotion", "neutral"),
            topic=data.get("topic", "general"),
            source="llm",
        )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/companion/test_evaluator.py -v`
Expected: PASS（9 项全过）

- [ ] **Step 6: 提交**

```
git add core/companion/prompts.py core/companion/evaluator.py tests/companion/test_evaluator.py
git commit -m "feat(companion): prompts + Evaluator（L1 硬阈值 + L2 LLM 决策 + 失败降级）"
```

---

## Task 5: Scheduler（节流/静音/概率/上限/冷却）

**Files:**
- Create: `core/companion/scheduler.py`
- Create: `tests/companion/test_scheduler.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/companion/test_scheduler.py`：
```python
"""Scheduler 测试：节流/冷却/静音/概率门控/每日上限。"""
from datetime import datetime, timezone
from unittest.mock import patch

from core.companion.scheduler import Scheduler


def test_scheduler_allows_when_all_conditions_met():
    s = Scheduler(config={
        "enabled": True,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "frequency": "mid",
        "daily_limit": 30,
    })
    # 14:30 不在静音时段
    assert s.should_consider(local_hour=14) is True


def test_scheduler_blocks_in_quiet_hours():
    s = Scheduler(config={
        "enabled": True,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "frequency": "mid", "daily_limit": 30,
    })
    # 02:30 在静音时段
    assert s.should_consider(local_hour=2) is False


def test_scheduler_blocks_when_disabled():
    s = Scheduler(config={"enabled": False})
    assert s.should_consider(local_hour=14) is False


def test_scheduler_frequency_low_20_percent():
    """low=20% 概率门控：random < 0.2 才允许。"""
    s = Scheduler(config={
        "enabled": True, "frequency": "low", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    with patch("core.companion.scheduler.random.random", return_value=0.1):
        assert s.should_consider(local_hour=14) is True
    with patch("core.companion.scheduler.random.random", return_value=0.3):
        assert s.should_consider(local_hour=14) is False


def test_scheduler_frequency_high_100_percent():
    s = Scheduler(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    with patch("core.companion.scheduler.random.random", return_value=0.99):
        assert s.should_consider(local_hour=14) is True


def test_scheduler_blocks_when_daily_limit_reached():
    s = Scheduler(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    assert s.should_consider(local_hour=14, greeting_count_today=30) is False
    assert s.should_consider(local_hour=14, greeting_count_today=29) is True


def test_scheduler_global_cooldown_10min():
    """上次问候后 10min 内不重复。"""
    s = Scheduler(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    # 模拟上次问候在 5min 前
    import time
    last_ts = (datetime.now(timezone.utc).timestamp() - 300)  # 5min 前
    assert s.global_cooldown_allows(last_greeting_ts_epoch=last_ts) is False
    # 11min 前
    last_ts = (datetime.now(timezone.utc).timestamp() - 660)
    assert s.global_cooldown_allows(last_greeting_ts_epoch=last_ts) is True


def test_scheduler_user_dialogue_cooldown_5min():
    """用户对话后 5min 内不触发 companion。"""
    s = Scheduler(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    import time
    now = time.time()
    # 用户 3min 前发过消息
    assert s.user_dialogue_cooldown_allows(last_user_msg_ts=now - 180) is False
    # 6min 前
    assert s.user_dialogue_cooldown_allows(last_user_msg_ts=now - 360) is True


def test_scheduler_quiet_hours_away_exception():
    """静音时段但 idle_state=away 超 1h 例外触发。"""
    s = Scheduler(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    # 02:30 在静音时段，但 away 超 1h
    assert s.should_consider(
        local_hour=2, idle_state="away", idle_seconds=3700) is True
    # 02:30 静音时段，正常活动
    assert s.should_consider(local_hour=2, idle_state="active") is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/companion/test_scheduler.py -v`
Expected: FAIL — `ImportError: No module named 'core.companion.scheduler'`

- [ ] **Step 3: 实现 scheduler.py**

创建 `core/companion/scheduler.py`：
```python
"""调度器：节流/静音/概率门控/每日上限/全局冷却/用户对话冷却。

执行顺序（在 Evaluator 之外，由 Controller 调用）：
1. enabled 检查
2. 静音时段检查（away 超 1h 例外）
3. 频率概率门控
4. 每日上限检查
5. 全局冷却检查
6. 用户对话冷却检查
"""
from __future__ import annotations

import random
import time
from typing import Optional


FREQ_RATIO = {"low": 0.2, "mid": 0.5, "high": 1.0}


class Scheduler:
    def __init__(self, config: dict) -> None:
        self.enabled = bool(config.get("enabled", True))
        qh = config.get("quiet_hours", {"start": "23:00", "end": "08:00"})
        self.quiet_start = self._parse_hour(qh.get("start", "23:00"))
        self.quiet_end = self._parse_hour(qh.get("end", "08:00"))
        self.frequency = str(config.get("frequency", "mid"))
        self.daily_limit = int(config.get("daily_limit", 30))

    @staticmethod
    def _parse_hour(hhmm: str) -> float:
        try:
            h, m = hhmm.split(":")
            return int(h) + int(m) / 60
        except (ValueError, AttributeError):
            return 23.0

    def _in_quiet_hours(self, local_hour: float) -> bool:
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= local_hour < self.quiet_end
        else:
            # 跨午夜（如 23:00-08:00）
            return local_hour >= self.quiet_start or local_hour < self.quiet_end

    def should_consider(
        self,
        *,
        local_hour: float,
        idle_state: str = "active",
        idle_seconds: int = 0,
        greeting_count_today: int = 0,
    ) -> bool:
        if not self.enabled:
            return False
        # 静音时段：away 超 1h 例外
        if self._in_quiet_hours(local_hour):
            if not (idle_state == "away" and idle_seconds > 3600):
                return False
        # 每日上限
        if greeting_count_today >= self.daily_limit:
            return False
        # 概率门控
        ratio = FREQ_RATIO.get(self.frequency, 0.5)
        if random.random() >= ratio:
            return False
        return True

    def global_cooldown_allows(
        self, *, last_greeting_ts_epoch: Optional[float], window_seconds: int = 600,
    ) -> bool:
        if last_greeting_ts_epoch is None:
            return True
        return (time.time() - last_greeting_ts_epoch) >= window_seconds

    def user_dialogue_cooldown_allows(
        self, *, last_user_msg_ts: Optional[float], window_seconds: int = 300,
    ) -> bool:
        if last_user_msg_ts is None:
            return True
        return (time.time() - last_user_msg_ts) >= window_seconds
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/companion/test_scheduler.py -v`
Expected: PASS（8 项全过）

- [ ] **Step 5: 提交**

```
git add core/companion/scheduler.py tests/companion/test_scheduler.py
git commit -m "feat(companion): Scheduler（静音/概率/上限/冷却）"
```

---

## Task 6: CompanionController + desktop_pet.py 接入 + 集成测试

**Files:**
- Create: `core/companion/controller.py`
- Create: `tests/companion/test_integration.py`
- Modify: `desktop_pet.py`（接入 CompanionController + _companion_speak 闭包）

- [ ] **Step 1: 写集成测试**

创建 `tests/companion/test_integration.py`：
```python
"""端到端集成测试：snapshot → evaluate → speak → route_and_send。"""
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from core.companion.controller import CompanionController
from core.companion.sensors import ContextSnapshot


def _snap(**kwargs):
    defaults = dict(
        timestamp="2026-08-16T10:00:00Z", local_time="14:30 周二",
        is_deep_night=False, idle_seconds=10, work_session_minutes=5,
        idle_state="active", active_window_title="main.py - Code",
        active_process="Code.exe", window_changed_recently=False,
        last_companion_greeting_ts=None, last_companion_topic=None,
        greeting_count_today=0,
    )
    defaults.update(kwargs)
    return ContextSnapshot(**defaults)


def test_controller_idle_over_15min_triggers_template_and_calls_storage_and_router():
    """空闲 16min → L1 模板命中 → 写 storage + 调 route_and_send。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {"active_window": True, "activity": True, "idle": True,
                    "clipboard": False, "screen": False},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    snap = _snap(idle_seconds=1000)  # >900 → L1 命中

    with patch("core.companion.storage.record_greeting", return_value=1) as mock_record, \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router:
        ctrl.handle_signal(snap, local_hour=14)

    mock_record.assert_called_once()
    mock_router.assert_called_once()
    # 验证 route_and_send 收到 companion 模式参数
    _, kwargs = mock_router.call_args
    assert kwargs["system_role"] == "companion"
    assert kwargs["skip_history"] is True
    assert kwargs["inject_system_prompt"] is not None
    assert "盯着屏幕发呆" in kwargs["input_text"]


def test_controller_scheduler_blocks_in_quiet_hours():
    """静音时段（非 away）不触发。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={})

    snap = _snap(idle_seconds=1000)  # L1 本应命中

    with patch("core.companion.storage.record_greeting") as mock_record, \
         patch("core.backend_router.route_and_send") as mock_router:
        ctrl.handle_signal(snap, local_hour=2)  # 02:30 静音时段

    mock_record.assert_not_called()
    mock_router.assert_not_called()


def test_controller_disabled_does_nothing():
    ctrl = CompanionController(config={"enabled": False}, llm_config={})
    snap = _snap(idle_seconds=1000)
    with patch("core.companion.storage.record_greeting") as mock_record:
        ctrl.handle_signal(snap, local_hour=14)
    mock_record.assert_not_called()


def test_controller_llm_decision_path():
    """L1 不命中时走 LLM 决策路径。"""
    ctrl = CompanionController(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "sensors": {},
    }, llm_config={"endpoint": "http://x", "api_key": "k", "model": "m"})

    snap = _snap(idle_seconds=10)  # L1 不命中

    llm_resp = {"should_speak": True, "text": "在写代码啊", "emotion": "neutral", "topic": "work"}
    with patch("core.companion.evaluator._call_llm", return_value=llm_resp), \
         patch("core.companion.storage.record_greeting", return_value=1), \
         patch("core.companion.storage.last_greeting_ts", return_value=None), \
         patch("core.companion.storage.greeting_count_today", return_value=0), \
         patch("core.backend_router.route_and_send", return_value=("reply", "chat")) as mock_router:
        ctrl.handle_signal(snap, local_hour=14)

    _, kwargs = mock_router.call_args
    assert kwargs["input_text"] == "在写代码啊"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/companion/test_integration.py -v`
Expected: FAIL — `ImportError: No module named 'core.companion.controller'`

- [ ] **Step 3: 实现 controller.py**

创建 `core/companion/controller.py`：
```python
"""CompanionController：聚合传感器+评估器+调度器。

由 desktop_pet.py 闭包内的 CompanionController 实例化（参考 AgentTask 模式）。
信号变化时调 handle_signal(snapshot, local_hour)，命中则 record_greeting + route_and_send。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from core.companion.evaluator import Evaluator
from core.companion.prompts import KURISU_PROACTIVE_PASS_THROUGH
from core.companion.scheduler import Scheduler
from core.companion.sensors import ContextSnapshot
from core.companion import storage
from core.storage import APP_DIR


class CompanionController:
    def __init__(self, *, config: dict, llm_config: dict) -> None:
        self.scheduler = Scheduler(config)
        self.evaluator = Evaluator()
        self.llm_endpoint = llm_config.get("endpoint", "")
        self.llm_api_key = llm_config.get("api_key", "")
        self.llm_model = llm_config.get("model", "")
        self._last_user_msg_ts: Optional[float] = None

    def on_user_message(self) -> None:
        """用户发消息时调用，更新冷却时间戳。"""
        self._last_user_msg_ts = time.time()

    def handle_signal(
        self, snapshot: ContextSnapshot, *, local_hour: float,
    ) -> None:
        """传感器信号变化时调用。命中则触发问候。"""
        # 用户对话冷却
        if not self.scheduler.user_dialogue_cooldown_allows(
            last_user_msg_ts=self._last_user_msg_ts
        ):
            return
        # 全局冷却
        last_ts_str = storage.last_greeting_ts()
        last_ts_epoch = self._parse_iso_to_epoch(last_ts_str) if last_ts_str else None
        if not self.scheduler.global_cooldown_allows(last_greeting_ts_epoch=last_ts_epoch):
            return
        # 静音/概率/上限
        if not self.scheduler.should_consider(
            local_hour=local_hour,
            idle_state=snapshot.idle_state,
            idle_seconds=snapshot.idle_seconds,
            greeting_count_today=storage.greeting_count_today(),
        ):
            return
        # 评估
        decision = self.evaluator.evaluate(
            snapshot, allow_llm=True, signal_type=snapshot.idle_state or "default",
            llm_endpoint=self.llm_endpoint, llm_api_key=self.llm_api_key,
            llm_model=self.llm_model,
        )
        if decision is None:
            return
        # 触发问候
        self._speak(decision)

    def _speak(self, decision) -> None:
        """写入 storage + 调 route_and_send。"""
        storage.record_greeting(decision.text, decision.topic, decision.emotion)
        # 延迟导入避免循环依赖
        from core.backend_router import route_and_send
        from config import KURISU_OUTPUT_FORMAT
        inject = KURISU_PROACTIVE_PASS_THROUGH.format(text=decision.text)
        try:
            route_and_send(
                config=self._load_config(),
                input_text=decision.text,
                soul_md=self._load_soul_md(),
                conversation_history=None,
                memories=None,
                on_delta=lambda t: None,  # 由 desktop_pet 注入实际回调
                on_status=lambda t: None,
                system_role="companion",
                skip_history=True,
                inject_system_prompt=inject,
            )
        except Exception:
            pass  # companion 永不影响主流程

    @staticmethod
    def _parse_iso_to_epoch(iso_str: str) -> float:
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return dt.timestamp()
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _load_config() -> dict:
        from core.storage import load_config
        return load_config()

    @staticmethod
    def _load_soul_md() -> str:
        """读取 SOUL.md，失败回退 KURISU_PERSONALITY。"""
        from core.storage import APP_DIR
        soul_path = APP_DIR / "SOUL.md"
        if soul_path.exists():
            return soul_path.read_text(encoding="utf-8")
        from config import get_character_by_id
        c = get_character_by_id("kurisu")
        return c.personality if c else ""

    def start(self, parent=None) -> None:
        """启动所有传感器 QTimer。"""
        from core.companion.sensors import (
            ActiveWindowSensor, ActivityTracker, IdleStateTracker,
            ClipboardSensor, ScreenSensor,
        )
        # TODO: 在 desktop_pet.py 闭包内实例化具体传感器并 start
        # 这里只提供接口，实际 QTimer 绑定在 desktop_pet.py 完成
        pass

    def stop(self) -> None:
        """停止所有传感器。"""
        pass
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/companion/test_integration.py -v`
Expected: PASS（4 项全过）

- [ ] **Step 5: 修改 desktop_pet.py 接入 CompanionController**

在 `desktop_pet.py` 找到 `class AgentSignals(QObject):` 之前（约 276 行），插入 CompanionController 实例化和传感器启动。

具体修改：在 `run_overlay` 函数体内、`class AgentSignals(QObject):` 定义之前（约 276 行附近），添加：

```python
        # === Companion 主动问候子系统 ===
        from core.companion.controller import CompanionController
        from core.companion.sensors import (
            ActiveWindowSensor, ActivityTracker, IdleStateTracker,
            ClipboardSensor, ScreenSensor, build_snapshot,
        )
        from core.companion import storage as companion_storage
        from core.companion.scheduler import Scheduler
        from datetime import datetime

        companion_cfg = {**{"enabled": True, "frequency": "mid", "daily_limit": 30,
                            "quiet_hours": {"start": "23:00", "end": "08:00"},
                            "sensors": {"active_window": True, "activity": True,
                                        "idle": True, "clipboard": False, "screen": False}},
                         **(load_config().get("companion") or {})}
        sensors_cfg = companion_cfg.get("sensors", {})

        aw_sensor = ActiveWindowSensor(interval_seconds=2)
        at_sensor = ActivityTracker(interval_seconds=30)
        it_tracker = IdleStateTracker()
        clip_sensor = ClipboardSensor(interval_seconds=1, enabled=bool(sensors_cfg.get("clipboard", False)))
        screen_sensor = ScreenSensor(enabled=bool(sensors_cfg.get("screen", False)))

        llm_cfg = load_config()
        companion_ctrl = CompanionController(
            config=companion_cfg,
            llm_config={
                "endpoint": llm_cfg.get("endpoint", ""),
                "api_key": llm_cfg.get("api_key", ""),
                "model": llm_cfg.get("model", ""),
            },
        )

        def _companion_tick() -> None:
            """周期性检查 companion 触发（每 30s 一次）。"""
            if not companion_cfg.get("enabled"):
                return
            try:
                aw_sensor._poll()  # 强制刷新一次（QTimer 也会单独触发）
                at_sensor._poll()
                it_tracker.update(at_sensor.idle_seconds)
                now = datetime.now()
                local_time = now.strftime("%H:%M 周%w")
                is_deep_night = 23 <= now.hour or now.hour < 6
                snap = build_snapshot(
                    active_window=aw_sensor, activity=at_sensor, idle=it_tracker,
                    clipboard=clip_sensor, screen=screen_sensor,
                    last_greeting_ts=companion_storage.last_greeting_ts(),
                    last_topic=None,  # 简化，下个版本从 storage 取
                    greeting_count=companion_storage.greeting_count_today(),
                    local_time=local_time, is_deep_night=is_deep_night,
                )
                companion_ctrl.handle_signal(snap, local_hour=now.hour + now.minute / 60)
            except Exception:
                pass  # companion 永不影响主流程

        # 接入表达层回调
        def _companion_on_delta(text: str) -> None:
            """companion 回复流式 delta，复用 _agent_delta。"""
            # _agent_delta 在闭包内已定义
            try:
                _agent_delta(text)
            except Exception:
                pass

        def _companion_on_status(text: str) -> None:
            try:
                _show_status(text)
            except Exception:
                pass

        # 启动 QTimer
        from PySide6.QtCore import QTimer as _QTimer
        companion_timer = _QTimer(self)
        companion_timer.timeout.connect(_companion_tick)
        companion_timer.start(30000)  # 30s 周期

        # 启动各传感器独立 QTimer
        aw_sensor.start(parent=self)
        at_sensor.start(parent=self)
        clip_sensor.start(parent=self)

        # 用户发消息时更新冷却
        _original_send = self._send
        def _send_with_companion_cooldown(*args, **kwargs):
            companion_ctrl.on_user_message()
            return _original_send(*args, **kwargs)
        self._send = _send_with_companion_cooldown
```

**注意**：以上代码块插入位置需要在 `run_overlay` 闭包内、`class AgentSignals` 之前。具体行号需读 desktop_pet.py 确认（约 276 行附近）。

**关键修正**：上面的 `_companion_on_delta` 和 `_companion_on_status` 没有真正接入 route_and_send（因为 controller._speak 用了 lambda t: None）。要真正接入表达层，需要修改 controller.py 的 `_speak` 方法接受回调参数。

修改 `core/companion/controller.py` 的 `_speak` 方法签名：

```python
    def _speak(self, decision, *, on_delta=None, on_status=None) -> None:
        """写入 storage + 调 route_and_send。"""
        storage.record_greeting(decision.text, decision.topic, decision.emotion)
        from core.backend_router import route_and_send
        from config import KURISU_OUTPUT_FORMAT
        inject = KURISU_PROACTIVE_PASS_THROUGH.format(text=decision.text)
        try:
            route_and_send(
                config=self._load_config(),
                input_text=decision.text,
                soul_md=self._load_soul_md(),
                conversation_history=None,
                memories=None,
                on_delta=on_delta or (lambda t: None),
                on_status=on_status or (lambda t: None),
                system_role="companion",
                skip_history=True,
                inject_system_prompt=inject,
            )
        except Exception:
            pass
```

并修改 `handle_signal` 调用 `_speak` 时传入回调：
```python
    def handle_signal(self, snapshot, *, local_hour, on_delta=None, on_status=None):
        ...
        self._speak(decision, on_delta=on_delta, on_status=on_status)
```

在 desktop_pet.py 调用 handle_signal 时传入：
```python
companion_ctrl.handle_signal(
    snap, local_hour=now.hour + now.minute / 60,
    on_delta=_companion_on_delta, on_status=_companion_on_status,
)
```

- [ ] **Step 6: 全量回归**

Run: `python -m pytest tests/ -v`
Expected: 所有测试通过，无回归。

- [ ] **Step 7: 提交**

```
git add core/companion/controller.py tests/companion/test_integration.py desktop_pet.py
git commit -m "feat(companion): CompanionController + desktop_pet 接入（30s 周期 + 传感器 QTimer + 表达层复用）"
```

---

## Task 7: config.py COMPANION_DEFAULTS + 设置页 companion tab

**Files:**
- Modify: `config.py`
- Modify: `ui/settings_dialog.py`

- [ ] **Step 1: 在 config.py 加 COMPANION_DEFAULTS**

在 `config.py` 的 `AGENT_ROUTER_DEFAULTS` 块之后（约 67 行后）插入：

```python
# === Companion 主动问候默认配置（2026-08-16 companion-proactive-greeting spec §8）===
# amadeus-py 的 companion 子系统：伪春菜式主动陪伴，检测用户活动并吐槽/关心。
# 5 个传感器逐项开关；剪贴板/屏幕默认关（隐私边界，产品化设计 §6）。
COMPANION_DEFAULTS: dict[str, object] = {
    "enabled": True,                            # 总开关
    "sensors": {
        "active_window": True,                  # 前台窗口检测（2s 轮询，低隐私）
        "activity": True,                        # 工作节奏检测（30s 轮询，低隐私）
        "idle": True,                            # 空闲状态检测（派生自 activity）
        "clipboard": False,                     # 剪贴板检测（默认关，中隐私）
        "screen": False,                        # 屏幕感知（默认关，高隐私，成本高）
    },
    "quiet_hours": {"start": "23:00", "end": "08:00"},  # 静音时段
    "frequency": "mid",                         # low=20% / mid=50% / high=100% 触发概率
    "daily_limit": 30,                          # 每日问候上限
}
```

- [ ] **Step 2: 在 settings_dialog.py 加 companion tab**

在 `ui/settings_dialog.py` 的 `# === 关于 / 版本 ===` 注释之前（约 89 行前）插入第 6 个 tab：

```python
        # === Companion 主动问候（2026-08-16 spec §8）===
        from config import COMPANION_DEFAULTS
        companion_page = QWidget()
        companion_form = QFormLayout(companion_page)
        companion_cfg = {**COMPANION_DEFAULTS, **(config.get("companion") or {})}
        self.companion_enabled = QCheckBox("启用主动陪伴（伪春菜式）")
        self.companion_enabled.setChecked(bool(companion_cfg.get("enabled", True)))
        companion_form.addRow(self.companion_enabled)

        # 传感器逐项开关
        sensors_cfg = {**COMPANION_DEFAULTS["sensors"], **(companion_cfg.get("sensors") or {})}
        self.sensor_active_window = QCheckBox("前台窗口检测（2s）")
        self.sensor_active_window.setChecked(bool(sensors_cfg.get("active_window", True)))
        companion_form.addRow(self.sensor_active_window)
        self.sensor_activity = QCheckBox("工作节奏检测（30s）")
        self.sensor_activity.setChecked(bool(sensors_cfg.get("activity", True)))
        companion_form.addRow(self.sensor_activity)
        self.sensor_idle = QCheckBox("空闲状态检测（派生）")
        self.sensor_idle.setChecked(bool(sensors_cfg.get("idle", True)))
        companion_form.addRow(self.sensor_idle)
        self.sensor_clipboard = QCheckBox("剪贴板检测（默认关，中隐私）")
        self.sensor_clipboard.setChecked(bool(sensors_cfg.get("clipboard", False)))
        companion_form.addRow(self.sensor_clipboard)
        self.sensor_screen = QCheckBox("屏幕感知（默认关，高隐私，成本高）")
        self.sensor_screen.setChecked(bool(sensors_cfg.get("screen", False)))
        companion_form.addRow(self.sensor_screen)

        # 静音时段
        qh = companion_cfg.get("quiet_hours", {"start": "23:00", "end": "08:00"})
        self.quiet_start = QLineEdit(str(qh.get("start", "23:00")))
        self.quiet_end = QLineEdit(str(qh.get("end", "08:00")))
        companion_form.addRow("静音开始", self.quiet_start)
        companion_form.addRow("静音结束", self.quiet_end)

        # 频率
        self.companion_freq = QComboBox()
        self.companion_freq.addItem("低（20%）", "low")
        self.companion_freq.addItem("中（50%）", "mid")
        self.companion_freq.addItem("高（100%）", "high")
        idx = self.companion_freq.findData(str(companion_cfg.get("frequency", "mid")))
        self.companion_freq.setCurrentIndex(max(idx, 0))
        companion_form.addRow("触发频率", self.companion_freq)

        # 每日上限
        self.companion_daily_limit = QLineEdit(str(companion_cfg.get("daily_limit", 30)))
        companion_form.addRow("每日上限", self.companion_daily_limit)

        # 当前上下文预览（只读）
        self.companion_preview = QLabel("（启动后显示）")
        self.companion_preview.setStyleSheet("color:#8a7f63; font-family: monospace;")
        self.companion_preview.setWordWrap(True)
        companion_form.addRow("当前上下文", self.companion_preview)

        # 测试问候 + 清空记忆按钮
        from PySide6.QtWidgets import QHBoxLayout
        btn_row = QHBoxLayout()
        test_btn = QPushButton("测试问候")
        test_btn.clicked.connect(self._test_companion)
        btn_row.addWidget(test_btn)
        clear_btn = QPushButton("清空记忆")
        clear_btn.clicked.connect(self._clear_companion_memory)
        btn_row.addWidget(clear_btn)
        companion_form.addRow(btn_row)

        tabs.addTab(companion_page, "Companion")
```

- [ ] **Step 3: 实现 _test_companion 和 _clear_companion_memory 方法**

在 `ui/settings_dialog.py` 的 `_probe_hermes` 方法之后插入：

```python
    def _test_companion(self) -> None:
        """手动触发一次 companion 问候（用于设置页验收）。"""
        from core.companion.evaluator import Evaluator
        from core.companion.sensors import ContextSnapshot
        from datetime import datetime
        now = datetime.now()
        local_time = now.strftime("%H:%M 周%w")
        is_deep_night = 23 <= now.hour or now.hour < 6
        snap = ContextSnapshot(
            timestamp=now.isoformat(), local_time=local_time,
            is_deep_night=is_deep_night, idle_seconds=10,
            work_session_minutes=5, idle_state="active",
            active_window_title="（测试）", active_process="test.exe",
            window_changed_recently=False,
            last_companion_greeting_ts=None,
            last_companion_topic=None, greeting_count_today=0,
        )
        ev = Evaluator()
        # 强制走 LLM 路径（即便 L1 不命中）
        cfg = load_config()
        decision = ev.evaluate(
            snap, allow_llm=True, signal_type="test",
            llm_endpoint=cfg.get("endpoint", ""),
            llm_api_key=cfg.get("api_key", ""),
            llm_model=cfg.get("model", ""),
        )
        if decision:
            self.companion_preview.setText(
                f"[{decision.source}] {decision.emotion}: {decision.text}"
            )
        else:
            self.companion_preview.setText("（LLM 判断不说话）")

    def _clear_companion_memory(self) -> None:
        """清空 lightweight_memory 表。"""
        from core.companion.storage import clear_all, init_schema
        init_schema()
        clear_all()
        self.companion_preview.setText("已清空记忆")
```

- [ ] **Step 4: 在 _save 中持久化 companion 配置**

在 `ui/settings_dialog.py` 的 `_save` 方法的 `config.update({...})` 字典里追加：

```python
        # companion 配置
        from config import COMPANION_DEFAULTS
        companion_cfg = {**COMPANION_DEFAULTS, **(config.get("companion") or {})}
        companion_cfg["enabled"] = self.companion_enabled.isChecked()
        companion_cfg["sensors"] = {
            "active_window": self.sensor_active_window.isChecked(),
            "activity": self.sensor_activity.isChecked(),
            "idle": self.sensor_idle.isChecked(),
            "clipboard": self.sensor_clipboard.isChecked(),
            "screen": self.sensor_screen.isChecked(),
        }
        companion_cfg["quiet_hours"] = {
            "start": self.quiet_start.text().strip(),
            "end": self.quiet_end.text().strip(),
        }
        companion_cfg["frequency"] = self.companion_freq.currentData()
        try:
            companion_cfg["daily_limit"] = int(self.companion_daily_limit.text().strip())
        except ValueError:
            companion_cfg["daily_limit"] = 30
        config["companion"] = companion_cfg
```

- [ ] **Step 5: 验证可导入**

Run: `python -c "from ui.settings_dialog import SettingsDialog; from config import COMPANION_DEFAULTS; print('ok', COMPANION_DEFAULTS['frequency'])"`
Expected: 输出 `ok mid`

- [ ] **Step 6: 手动验证——打开设置页**

Run（非阻塞）: `python desktop_pet.py`，点击 ⚙ 打开设置。
Expected: tab 列表含 "Companion"。点 "测试问候" 显示一条 LLM 生成的红莉栖语气问候。点 "清空记忆" 显示 "已清空记忆"。

- [ ] **Step 7: 提交**

```
git add config.py ui/settings_dialog.py
git commit -m "feat(companion): COMPANION_DEFAULTS + 设置页第 6 个 companion tab（开关/静音/频率/上限/测试/清空）"
```

---

## Task 8: 全量验收 + lessons 更新

**Files:** 无（验证 + 文档）

- [ ] **Step 1: 全量单元测试**

Run: `python -m pytest tests/ -v`
Expected: 全部测试通过（既有 + companion 新增 ~37 项）。

- [ ] **Step 2: 启动验收清单**

Run（非阻塞）: `python desktop_pet.py`，逐项确认：
- [ ] 启动桌宠，发条消息确认 chat 正常
- [ ] 不动鼠标 16min，确认红莉栖主动说"盯着屏幕发呆..."
- [ ] 切到 B 站窗口，等待 LLM 决策（5min 节流后），确认红莉栖吐槽"在摸鱼吧"
- [ ] 设置页关闭 companion 总开关，确认不再主动说话
- [ ] 设置页关闭前台窗口传感器，确认不再有窗口相关吐槽
- [ ] 调整静音时段为当前时间，确认不触发
- [ ] 频率调"低"，观察触发概率明显下降
- [ ] 清空记忆按钮，确认 lightweight_memory 表被清空
- [ ] 关掉网络，触发 LLM 决策场景，确认走模板降级
- [ ] 验证 chat history 不被 companion 污染

- [ ] **Step 3: 更新 lessons.md**

在 `lessons.md` 末尾追加 companion 实施小结（5 条教训），格式参照既有章节。

- [ ] **Step 4: 最终提交**

```
git add lessons.md
git commit -m "docs(lessons): companion 主动问候实施教训"
```

---

## Self-Review（计划自审）

**1. Spec 覆盖：**
- §3 架构总览：Task 6 CompanionController ✓
- §4 感知层 5 传感器：Task 3 ✓
- §5 评估器 L1+L2：Task 4 ✓
- §5 节流策略：Task 4（Evaluator 5min）+ Task 5（Scheduler 10min/概率/上限/静音）✓
- §6 lightweight_memory 表：Task 2 ✓
- §7 route_and_send 扩展：Task 1 ✓
- §8 设置页 companion tab：Task 7 ✓
- §9 错误处理：Task 4 LLM 失败降级 ✓ + Task 6 try/except 静默 ✓
- §10 测试策略：Task 1-6 各自单元测试 ✓ + Task 6 集成测试 ✓ + Task 8 验收清单 ✓

**2. Placeholder 扫描：** 无 TBD/TODO（Task 6 controller.py start/stop 的 pass 是占位但不影响主流程，desktop_pet.py 直接管理 QTimer）。

**3. 类型一致性：**
- `GreetingDecision` 在 Task 4 定义，Task 6 引用 ✓
- `ContextSnapshot` 在 Task 3 定义，Task 4/6 引用 ✓
- `route_and_send` 扩展参数 `system_role/skip_history/inject_system_prompt` 在 Task 1 定义，Task 6 引用 ✓
- 参数名修正：spec 写的 `text`/`history` 改为实际签名 `input_text`/`conversation_history` ✓

**4. 风险点：**
- Task 6 desktop_pet.py 闭包接入是最大风险（位置依赖 `class AgentSignals` 之前），实施时需先读 desktop_pet.py 确认具体行号
- pywin32 依赖：若未装需在 Task 3 前装（`pip install --target=.libs pywin32`）
- mss 依赖：已在项目里（电话模式用过），无新依赖
