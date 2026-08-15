# Agent 模式（Hermes/codex 双后端 + gate 分流）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 桌宠输入按模式/意图分流到 本地直连 / Hermes 网关 / codex 子进程 三个后端，Hermes 网关自动拉起，失败自动降级本地直连。

**Architecture:** 新增 `core/backend_router.py`（gate 分流 + 路由）、`core/hermes_launcher.py`（网关探活/拉起）、`core/codex_client.py`（codex exec 子进程 + AGENTS.md 人设）。`desktop_pet.py` 的 `AgentTask.run` 改调 `route_and_send`。设置页新增 Agent 模式 tab。

**Tech Stack:** Python 3.13 / httpx（已有）/ subprocess / PySide6（设置页）/ pytest

**设计文档:** `docs/superpowers/specs/2026-08-15-agent-mode-design.md`（commit 8b1f6a5）

**环境前置（已实测就绪）:** Hermes v0.20.0 + kurisu profile（API_SERVER_KEY 在 profile .env）；codex-cli 0.146.0 已登录。测试命令一律用 `D:\anaconda\python.exe -m pytest`（lessons：PATH 里的 python 是 hermes venv）。

---

### Task 1: config.py 增加 AGENT_ROUTER_DEFAULTS

**Files:**
- Modify: `config.py`（OPENCLAW_DEFAULTS 之后、PHONE_DEFAULTS 之前，约 L57）
- Test: `tests/test_agent_router_config.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_agent_router_config.py
"""AGENT_ROUTER_DEFAULTS 结构与合法值约束。"""
from config import AGENT_ROUTER_DEFAULTS


def test_defaults_shape():
    assert AGENT_ROUTER_DEFAULTS["mode"] == "chat"
    assert set(AGENT_ROUTER_DEFAULTS) == {"mode", "codex"}


def test_codex_defaults():
    codex = AGENT_ROUTER_DEFAULTS["codex"]
    assert codex["sandbox"] in ("read-only", "workspace-write")
    assert isinstance(codex["timeout"], int) and codex["timeout"] > 0
    assert isinstance(codex["workspace"], str) and codex["workspace"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_agent_router_config.py -v`
Expected: FAIL `ImportError: cannot import name 'AGENT_ROUTER_DEFAULTS'`

- [ ] **Step 3: 最小实现**

在 `config.py` 的 `OPENCLAW_DEFAULTS` 块之后插入：

```python
# === Agent 模式路由默认配置（2026-08-15 agent-mode spec §4.4）===
# mode: "chat"=本地直连(现状) | "hermes"=Hermes 网关(deepseek模式) | "codex"=codex 子进程 | "auto"=gate 分流
# 运行时被 data/config.json 的 agent_router 键覆盖（{**DEFAULTS, **config["agent_router"]}）。
AGENT_ROUTER_DEFAULTS: dict[str, object] = {
    "mode": "chat",
    "codex": {
        "workspace": "data/codex_workspace",   # AGENTS.md 与 codex 会话工作根目录（相对项目根）
        "sandbox": "read-only",                # codex 沙箱：read-only | workspace-write
        "timeout": 120,                        # codex exec 单轮超时（秒）
    },
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_agent_router_config.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_agent_router_config.py
git commit -m "feat(config): AGENT_ROUTER_DEFAULTS 路由默认配置（agent-mode Task1）"
```

---

### Task 2: core/hermes_launcher.py（探活/拉起/API key 读取）

**Files:**
- Create: `core/hermes_launcher.py`
- Test: `tests/test_hermes_launcher.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_hermes_launcher.py
"""hermes_launcher：探活/拉起逻辑（mock httpx + Popen，不打真网关）。"""
from unittest.mock import MagicMock

import httpx

from core import hermes_launcher
from core.hermes_launcher import ensure_gateway, probe_health, read_profile_api_key


def test_read_profile_api_key(tmp_path, monkeypatch):
    profile_dir = tmp_path / ".hermes" / "profiles" / "kurisu"
    profile_dir.mkdir(parents=True)
    (profile_dir / ".env").write_text(
        "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=abc123\n", encoding="utf-8")
    monkeypatch.setattr(hermes_launcher.Path, "home", staticmethod(lambda: tmp_path))
    assert read_profile_api_key("kurisu") == "abc123"


def test_read_profile_api_key_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(hermes_launcher.Path, "home", staticmethod(lambda: tmp_path))
    assert read_profile_api_key("kurisu") is None


def test_probe_health_ok(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value.get.return_value = MagicMock(status_code=200)
    monkeypatch.setattr(hermes_launcher.httpx, "Client", lambda **kw: client)
    assert probe_health("http://127.0.0.1:8642", "k") is True


def test_probe_health_conn_error(monkeypatch):
    def boom(**kw):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(hermes_launcher.httpx, "Client", boom)
    assert probe_health("http://127.0.0.1:8642") is False


def test_ensure_gateway_already_up():
    probe = MagicMock(return_value=True)
    popen = MagicMock()
    assert ensure_gateway(base_url="http://x", api_key="k", probe=probe, popen=popen) is True
    popen.assert_not_called()


def test_ensure_gateway_starts_and_waits(monkeypatch):
    probe = MagicMock(side_effect=[False, False, True])
    popen = MagicMock()
    monkeypatch.setattr(hermes_launcher.time, "sleep", lambda s: None)
    ok = ensure_gateway(base_url="http://x", probe=probe, popen=popen, wait_timeout=30)
    assert ok is True
    assert popen.call_count == 1
    argv = popen.call_args.args[0]
    assert argv[0] == "hermes" and argv[1] == "-p" and "gateway" in argv


def test_ensure_gateway_timeout(monkeypatch):
    probe = MagicMock(return_value=False)
    popen = MagicMock()
    monkeypatch.setattr(hermes_launcher.time, "sleep", lambda s: None)
    ok = ensure_gateway(base_url="http://x", probe=probe, popen=popen, wait_timeout=3)
    assert ok is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_hermes_launcher.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'core.hermes_launcher'`

- [ ] **Step 3: 实现 core/hermes_launcher.py**

```python
"""Hermes 网关生命周期：探活 / 拉起 / API key 同步。

设计依据 docs/superpowers/specs/2026-08-15-agent-mode-design.md §4.2：
- GET /health（Bearer）探活，2s 超时
- 不通 → Popen("hermes -p <profile> gateway") 分离进程，日志落 data/hermes_gateway.log
- 轮询探活最多 30s；仍失败由调用方（backend_router）降级本地直连
- 桌宠退出不杀网关（常驻，同 GPT-SoVITS 惯例）
probe/popen 参数为依赖注入，供测试 mock。
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

import httpx


def read_profile_api_key(profile: str = "kurisu") -> str | None:
    """从 ~/.hermes/profiles/<profile>/.env 读 API_SERVER_KEY。"""
    env_path = Path.home() / ".hermes" / "profiles" / profile / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("API_SERVER_KEY="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def probe_health(base_url: str, api_key: str = "", timeout: float = 2.0) -> bool:
    """GET /health，Bearer 认证（官方要求 key 必须，含 loopback 部署）。"""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{base_url.rstrip('/')}/health", headers=headers)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def ensure_gateway(
    *,
    base_url: str,
    api_key: str = "",
    profile: str = "kurisu",
    log_path: str | Path = "data/hermes_gateway.log",
    wait_timeout: float = 30.0,
    probe=None,
    popen=subprocess.Popen,
) -> bool:
    """探活 → 不通则拉起网关子进程 → 轮询探活。返回最终是否可用。"""
    probe = probe or probe_health
    if probe(base_url, api_key):
        return True
    log_file = Path(log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with log_file.open("ab") as fh:
        popen(
            ["hermes", "-p", profile, "gateway"],
            stdout=fh, stderr=fh, creationflags=flags,
            stdin=subprocess.DEVNULL,
        )
    deadline = time.monotonic() + wait_timeout
    while time.monotonic() < deadline:
        time.sleep(1.0)
        if probe(base_url, api_key):
            return True
    return False
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_hermes_launcher.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add core/hermes_launcher.py tests/test_hermes_launcher.py
git commit -m "feat(hermes): 网关探活/拉起/API key 读取（agent-mode Task2）"
```

---

### Task 3: core/codex_client.py（exec 子进程 + JSONL 适配 + AGENTS.md）

**Files:**
- Create: `core/codex_client.py`
- Test: `tests/test_codex_client.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_codex_client.py
"""codex_client：事件解析/追加语义/超时/退出码（FakePopen，不打真 codex）。

JSONL 事件 fixture 为宽容契约样本（真实 codex 输出可能多事件/多字段，
parse_event_line 对未知结构返回 None 才是契约核心）。
"""
import subprocess
import time

import pytest

from core.codex_client import ensure_agents_md, parse_event_line, run_codex_turn


class FakeProc:
    """stdout 为生成器：耗尽时置 returncode；hang=True 永不结束（测超时）。"""

    def __init__(self, lines, returncode=0, hang=False):
        self._lines = list(lines)
        self._rc = returncode
        self._hang = hang
        self.stdout = self._gen()
        self.returncode = None
        self.terminated = False

    def _gen(self):
        for ln in self._lines:
            yield ln
        if not self._hang:
            self.returncode = self._rc

    def wait(self, timeout=None):
        deadline = time.monotonic() + (timeout if timeout else 3600)
        while self.returncode is None:
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired("codex", timeout)
            time.sleep(0.001)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -9


EVENTS = [
    '{"id":"0","msg":{"type":"session_configured","session_id":"s1"}}',
    '{"type":"item.started","item":{"type":"agent_message"}}',
    '{"type":"item.completed","item":{"type":"agent_message","text":"你好"}}',
    '{"type":"item.completed","item":{"type":"command_execution","command":"dir","status":"done"}}',
    '{"type":"item.completed","item":{"type":"agent_message","text":"你好，我是红莉栖"}}',
    'not-json-noise',
]


def test_parse_event_line_delta():
    assert parse_event_line(EVENTS[2]) == ("delta", "你好")


def test_parse_event_line_status():
    kind, text = parse_event_line(EVENTS[3])
    assert kind == "status" and "command_execution" in text


def test_parse_event_line_noise():
    assert parse_event_line(EVENTS[5]) is None
    assert parse_event_line("") is None
    assert parse_event_line('{"type":"unknown_thing"}') is None


def test_ensure_agents_md(tmp_path):
    ws = tmp_path / "ws"
    path = ensure_agents_md(ws, "人设A", "格式B")
    assert path.exists()
    assert "人设A" in path.read_text(encoding="utf-8")
    path.write_text("KEEP", encoding="utf-8")  # 已存在不覆盖
    ensure_agents_md(ws, "人设A", "格式B")
    assert path.read_text(encoding="utf-8") == "KEEP"


def test_run_codex_turn_append_semantics(tmp_path):
    deltas, statuses = [], []
    reply = run_codex_turn(
        input_text="hi", workspace=tmp_path, popen=lambda a, **k: FakeProc(EVENTS),
        on_delta=deltas.append, on_status=statuses.append)
    assert deltas == ["你好", "，我是红莉栖"]  # 全量快照 → 增量（追加语义）
    assert any("command_execution" in s for s in statuses)
    assert reply == "你好，我是红莉栖"          # 无 -o 文件时回退最后 delta


def test_run_codex_turn_output_file_truth(tmp_path):
    (tmp_path / "codex_last.txt").write_text("最终答案", encoding="utf-8")
    reply = run_codex_turn(
        input_text="hi", workspace=tmp_path, popen=lambda a, **k: FakeProc(EVENTS))
    assert reply == "最终答案"                  # -o 产物文件是真相兜底


def test_run_codex_turn_timeout(tmp_path):
    proc = FakeProc(EVENTS, hang=True)
    with pytest.raises(RuntimeError, match="超时"):
        run_codex_turn(input_text="hi", workspace=tmp_path, timeout=0.2,
                       popen=lambda a, **k: proc)
    assert proc.terminated is True


def test_run_codex_turn_nonzero(tmp_path):
    with pytest.raises(RuntimeError, match="退出码"):
        run_codex_turn(input_text="hi", workspace=tmp_path,
                       popen=lambda a, **k: FakeProc(EVENTS, returncode=1))


def test_run_codex_turn_argv(tmp_path):
    calls = {}

    def popen(argv, **kw):
        calls["argv"] = argv
        return FakeProc(EVENTS)

    run_codex_turn(input_text="问题", workspace=tmp_path, resume=True, popen=popen)
    argv = calls["argv"]
    assert argv[0] == "codex" and "exec" in argv and "--json" in argv
    assert "resume" in argv and "--last" in argv
    assert argv[-1] == "问题"
    assert argv[argv.index("-s") + 1] == "read-only"
    assert "-C" in argv and "-o" in argv
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_codex_client.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'core.codex_client'`

- [ ] **Step 3: 实现 core/codex_client.py**

```python
"""codex 模式客户端：codex exec 子进程 + JSONL 事件适配 + AGENTS.md 人设。

设计依据 docs/superpowers/specs/2026-08-15-agent-mode-design.md §4.3：
- 首轮 `codex exec ... "<input>"`；后续加 `resume --last` 延续会话
- 角色一致性 = workspace/AGENTS.md（SOUL.md + KURISU_OUTPUT_FORMAT 生成）
- 最终回复以 -o 产物文件为真相兜底；JSONL 仅驱动 on_delta/on_status
- agent_message 事件是全量快照 → 增量转换（保持 on_delta 追加语义）
- 默认 read-only 沙箱；超时 terminate()
popen 参数为依赖注入，供测试 mock。
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading

AGENTS_MD_HEADER = """# Kurisu（牧瀬紅莉栖）— 桌宠人格指令

> 本文件由 amadeus-py 自动生成（codex 模式人设）。修改会被覆盖，请改 SOUL.md 源。

"""


def ensure_agents_md(workspace: Path, soul_md: str, output_format: str) -> Path:
    """workspace/AGENTS.md 不存在时写入（人设 + 输出格式）。返回其路径。"""
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "AGENTS.md"
    if not path.exists():
        path.write_text(
            AGENTS_MD_HEADER + soul_md.strip() + "\n\n---\n\n" + output_format.strip() + "\n",
            encoding="utf-8")
    return path


def parse_event_line(line: str) -> tuple[str, str] | None:
    """宽容解析一行 codex --json 事件：返回 (kind, text) 或 None。

    kind: "delta"（agent 消息文本）| "status"（工具/命令进展）。
    未识别结构一律返回 None（隔离 codex 版本间事件格式差异）。
    """
    line = line.strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    item = event.get("item") or event.get("msg") or {}
    if not isinstance(item, dict):
        return None
    item_type = str(item.get("type") or event.get("type") or "")
    if item_type == "agent_message":
        text = str(item.get("text") or "")
        return ("delta", text) if text else None
    if item_type in ("command_execution", "file_change", "mcp_tool_call",
                     "todo_list", "web_search", "view_image"):
        detail = item.get("command") or item.get("changes") or item.get("tool") \
            or item.get("status") or ""
        return ("status", f"codex: {item_type} {detail}".strip())
    if item_type in ("task_started", "task_complete", "session_configured", "turn_context"):
        return ("status", f"codex: {item_type}")
    return None


def run_codex_turn(
    *,
    input_text: str,
    workspace: Path,
    resume: bool = False,
    sandbox: str = "read-only",
    timeout: float = 120.0,
    on_delta=lambda text: None,
    on_status=lambda text: None,
    popen=subprocess.Popen,
) -> str:
    """跑一轮 codex exec，返回最终回复文本。失败抛 RuntimeError。"""
    workspace = Path(workspace)
    out_file = workspace / "codex_last.txt"
    argv = [
        "codex", "exec", "--json", "--skip-git-repo-check",
        "-s", sandbox, "-C", str(workspace), "-o", str(out_file),
    ]
    if resume:
        argv += ["resume", "--last"]
    argv.append(input_text)

    proc = popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                 text=True, encoding="utf-8", errors="replace", cwd=str(workspace))
    last_text = ""
    emitted = ""

    def _read() -> None:
        nonlocal last_text, emitted
        assert proc.stdout is not None
        for raw in proc.stdout:
            parsed = parse_event_line(raw)
            if not parsed:
                continue
            kind, text = parsed
            if kind == "delta":
                last_text = text
                if text.startswith(emitted) and len(text) > len(emitted):
                    on_delta(text[len(emitted):])   # 全量快照 → 增量
                    emitted = text
                elif not text.startswith(emitted):
                    on_delta("\n" + text)          # 罕见：快照被改写，整段补发
                    emitted = text
            else:
                on_status(text)

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        raise RuntimeError(f"codex 执行超时（{timeout:.0f}s）")
    reader.join(timeout=5)
    if proc.returncode != 0:
        raise RuntimeError(f"codex exec 退出码 {proc.returncode}")
    if out_file.exists():
        content = out_file.read_text(encoding="utf-8", errors="replace").strip()
        if content:
            return content                        # -o 产物文件是真相兜底
    if last_text:
        return last_text
    raise RuntimeError("codex 未产生任何回复文本")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_codex_client.py -v`
Expected: 9 passed

- [ ] **Step 5: 真机校准（非测试，采集样本供人工核对）**

Run: `D:\anaconda\python.exe -c "import subprocess; r = subprocess.run(['codex','exec','--json','--skip-git-repo-check','-s','read-only','-C','data','用一句话自我介绍'], capture_output=True, text=True, encoding='utf-8'); open('data/codex_events_sample.jsonl','w',encoding='utf-8').write(r.stdout)"`
然后 Read `data/codex_events_sample.jsonl` 人工核对：真实事件里 agent 消息类型是否为 `agent_message`、命令事件类型名是否在 parse_event_line 的 status 集合内。若不一致，仅调整 `parse_event_line` 的类型名映射（测试 fixture 同步更新），其余逻辑不动。

- [ ] **Step 6: Commit**

```bash
git add core/codex_client.py tests/test_codex_client.py
git commit -m "feat(codex): codex exec 子进程客户端 + AGENTS.md 人设（agent-mode Task3）"
```

---

### Task 4: core/backend_router.py（gate 分流 + 路由分发）

**Files:**
- Create: `core/backend_router.py`
- Test: `tests/test_backend_router.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_backend_router.py
"""backend_router：分类矩阵 + 分发/降级链（monkeypatch 后端函数，不打真 API）。"""
import pytest

from core import backend_router
from core.backend_router import classify_input


@pytest.mark.parametrize("text,expected", [
    ("你好", "chat"),
    ("晚上好呀", "chat"),
    ("帮我搜一下今天天气", "agent"),
    ("读一下 D 盘的文件", "agent"),
    ("打开记事本", "gui"),
    ("截个屏", "gui"),
])
def test_classify_rules(text, expected):
    assert classify_input(text, openclaw_enabled=True) == expected


def test_classify_gui_without_openclaw():
    # openclaw 未启用时 gui 意图不成立，落到 agent/chat
    assert classify_input("打开记事本", openclaw_enabled=False) in ("agent", "chat")


def test_classify_llm_injectable():
    assert classify_input("一段模糊的话", llm_classify=lambda t: "agent") == "agent"


def test_classify_llm_exception_defaults_chat():
    def boom(t):
        raise RuntimeError("net down")
    assert classify_input("一段模糊的话", llm_classify=boom) == "chat"


def test_classify_llm_invalid_value_defaults_chat():
    assert classify_input("一段模糊的话", llm_classify=lambda t: "乱值") == "chat"


def _cfg(mode, **kw):
    return {"agent_router": {"mode": mode, **kw},
            "endpoint": "http://x", "api_key": "k", "model": "m"}


def test_route_chat_uses_local(monkeypatch):
    import core.agent_client as ac
    monkeypatch.setattr(ac, "run_local_run", lambda **kw: "本地回复")
    reply, backend = backend_router.route_and_send(
        config=_cfg("chat"), input_text="你好", soul_md="soul")
    assert (reply, backend) == ("本地回复", "chat")


def test_route_hermes_ok(monkeypatch):
    import core.agent_client as ac
    import core.hermes_launcher as hl
    monkeypatch.setattr(hl, "read_profile_api_key", lambda p: "hk")
    monkeypatch.setattr(hl, "ensure_gateway", lambda **kw: True)
    monkeypatch.setattr(ac, "run_hermes_run", lambda **kw: "hermes 回复")
    reply, backend = backend_router.route_and_send(
        config=_cfg("hermes"), input_text="hi", soul_md="soul")
    assert (reply, backend) == ("hermes 回复", "hermes")


def test_route_hermes_gateway_down_fallback(monkeypatch):
    import core.agent_client as ac
    import core.hermes_launcher as hl
    monkeypatch.setattr(hl, "read_profile_api_key", lambda p: "hk")
    monkeypatch.setattr(hl, "ensure_gateway", lambda **kw: False)
    monkeypatch.setattr(ac, "run_local_run", lambda **kw: "本地回复")
    statuses = []
    reply, backend = backend_router.route_and_send(
        config=_cfg("hermes"), input_text="hi", soul_md="soul",
        on_status=statuses.append)
    assert backend == "chat"
    assert any("本地直连" in s for s in statuses)


def test_route_hermes_runerror_fallback(monkeypatch):
    import core.agent_client as ac
    import core.hermes_launcher as hl
    monkeypatch.setattr(hl, "read_profile_api_key", lambda p: "hk")
    monkeypatch.setattr(hl, "ensure_gateway", lambda **kw: True)

    def boom(**kw):
        raise RuntimeError("run.failed")

    monkeypatch.setattr(ac, "run_hermes_run", boom)
    monkeypatch.setattr(ac, "run_local_run", lambda **kw: "本地回复")
    reply, backend = backend_router.route_and_send(
        config=_cfg("hermes"), input_text="hi", soul_md="soul")
    assert (reply, backend) == ("本地回复", "chat")


def test_route_codex(monkeypatch, tmp_path):
    import core.codex_client as cc
    monkeypatch.setattr(backend_router, "_codex_session_started", False)
    monkeypatch.setattr(cc, "ensure_agents_md", lambda ws, s, o: ws / "AGENTS.md")
    monkeypatch.setattr(cc, "run_codex_turn", lambda **kw: "codex 回复")
    cfg = _cfg("codex", codex={"workspace": str(tmp_path)})
    reply, backend = backend_router.route_and_send(
        config=cfg, input_text="hi", soul_md="soul")
    assert (reply, backend) == ("codex 回复", "codex")
    assert backend_router._codex_session_started is True


def test_route_auto_uses_classify(monkeypatch):
    import core.agent_client as ac
    monkeypatch.setattr(ac, "run_local_run", lambda **kw: "ok")
    monkeypatch.setattr(backend_router, "classify_input", lambda text, **kw: "chat")
    reply, backend = backend_router.route_and_send(
        config=_cfg("auto"), input_text="hi", soul_md="soul")
    assert backend == "chat"


def test_route_gui_nudge_local(monkeypatch):
    import core.agent_client as ac
    seen = {}

    def fake_local(**kw):
        seen.update(kw)
        return "ok"

    monkeypatch.setattr(ac, "run_local_run", fake_local)
    monkeypatch.setattr(backend_router, "classify_input", lambda text, **kw: "gui")
    cfg = _cfg("auto")
    cfg["openclaw"] = {"enabled": True}
    reply, backend = backend_router.route_and_send(
        config=cfg, input_text="打开记事本", soul_md="soul")
    assert backend == "gui"
    assert "operate_gui" in seen["input_text"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_backend_router.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'core.backend_router'`

- [ ] **Step 3: 实现 core/backend_router.py**

```python
"""gate 分流 + 后端路由（spec §3 / §4.1）。

route_and_send 是 desktop_pet.AgentTask 的唯一入口：
按 agent_router.mode 分发到 chat(本地直连) / hermes / codex；
auto 模式先 classify_input（L1 规则 → L2 DeepSeek 意图分类）。
返回 (reply, backend_used)，hermes 失败自动降级本地直连。
"""
from __future__ import annotations

import json
from pathlib import Path
import re

import httpx

from config import AGENT_ROUTER_DEFAULTS, KURISU_OUTPUT_FORMAT, OPENCLAW_DEFAULTS

GUI_PATTERN = re.compile(
    r"打开|关闭|点击|截屏|截图|鼠标|键盘|双击|右键|操作.{0,6}(窗口|界面|软件|应用)")
AGENT_PATTERN = re.compile(
    r"搜索|查找文件|帮我(写|整理|运行|分析|找)|读.{0,4}文件|列出|下载|"
    r"运行(命令|脚本)|查一下|百度|google|联网|查天气|查新闻")
CHAT_HINT_PATTERN = re.compile(
    r"^(你好|早上好|中午好|晚上好|嗨|哈喽|在吗|嗯+|哦+|好呀?|晚安|再见|无聊|随便聊聊).*$")

GUI_NUDGE = "（用户想操作图形界面/应用，优先调用 operate_gui 工具完成。）"

_codex_session_started = False  # codex resume --last 会话状态（进程内）


def classify_input(text: str, *, openclaw_enabled: bool = False, llm_classify=None) -> str:
    """L1 规则 → L2 LLM（可注入）。返回 'chat' | 'agent' | 'gui'。失败默认 chat。"""
    text = (text or "").strip()
    if not text:
        return "chat"
    if len(text) <= 6 and CHAT_HINT_PATTERN.match(text):
        return "chat"
    if openclaw_enabled and GUI_PATTERN.search(text):
        return "gui"
    if AGENT_PATTERN.search(text):
        return "agent"
    if llm_classify is None:
        return "chat"
    try:
        result = llm_classify(text)
    except Exception:
        return "chat"
    return result if result in ("chat", "agent", "gui") else "chat"


def _llm_classify(text: str, *, endpoint: str, api_key: str, model: str) -> str | None:
    """DeepSeek 意图分类（非流式小请求）。失败返回 None。"""
    system = (
        "你是输入分流器。把用户输入分类为 JSON：{\"route\":\"chat|agent|gui\"}。"
        "chat=闲聊/问答；agent=需要工具/搜索/文件/命令；gui=需要操作图形界面（鼠标键盘窗口）。"
        "只输出 JSON。"
    )
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{endpoint.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ], "max_tokens": 50, "temperature": 0},
            )
        if resp.is_error:
            return None
        content = resp.json()["choices"][0]["message"]["content"]
        match = re.search(r"\{[^{}]*\}", content or "")
        if not match:
            return None
        route = json.loads(match.group(0)).get("route")
        return route if route in ("chat", "agent", "gui") else None
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        return None


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
    """按模式分发，返回 (reply, backend_used)。hermes 失败自动降级本地直连。"""
    global _codex_session_started
    from core.agent_client import run_hermes_run, run_local_run
    from core.codex_client import ensure_agents_md, run_codex_turn
    from core.hermes_launcher import ensure_gateway, read_profile_api_key
    from core.storage import APP_DIR

    router = {**AGENT_ROUTER_DEFAULTS, **(config.get("agent_router") or {})}
    mode = str(router.get("mode", "chat"))

    openclaw_cfg = dict(OPENCLAW_DEFAULTS)
    if isinstance(config.get("openclaw"), dict):
        openclaw_cfg.update(config["openclaw"])
    openclaw_enabled = bool(openclaw_cfg.get("enabled", False))

    route = mode if mode in ("chat", "hermes", "codex") else classify_input(
        input_text, openclaw_enabled=openclaw_enabled,
        llm_classify=lambda t: _llm_classify(
            t, endpoint=config.get("endpoint", ""),
            api_key=config.get("api_key", ""), model=config.get("model", "")),
    )

    hermes_cfg = {**{"base_url": "http://127.0.0.1:8642", "profile": "kurisu",
                     "session_id": "amadeus-kurisu", "api_key": ""},
                  **(config.get("hermes") or {})}
    base_url = str(hermes_cfg.get("base_url"))
    api_key = str(hermes_cfg.get("api_key") or "") \
        or (read_profile_api_key(str(hermes_cfg.get("profile"))) or "")

    if route == "hermes":
        if ensure_gateway(base_url=base_url, api_key=api_key,
                          profile=str(hermes_cfg.get("profile"))):
            try:
                reply = run_hermes_run(
                    base_url=base_url, api_key=api_key, input_text=input_text,
                    instructions=KURISU_OUTPUT_FORMAT,
                    conversation_history=conversation_history,
                    session_id=str(hermes_cfg.get("session_id")),
                    on_delta=on_delta, on_status=on_status, on_approval=on_approval,
                )
                return reply, "hermes"
            except RuntimeError:
                on_status("Hermes 调用失败，已切本地直连")
        else:
            on_status("Hermes 网关不可用，已切本地直连")

    elif route == "codex":
        codex_cfg = {**AGENT_ROUTER_DEFAULTS["codex"], **(router.get("codex") or {})}
        workspace = Path(str(codex_cfg.get("workspace", "data/codex_workspace")))
        if not workspace.is_absolute():
            workspace = APP_DIR.parent / workspace  # APP_DIR=<根>/data → 取项目根
        ensure_agents_md(workspace, soul_md, KURISU_OUTPUT_FORMAT)
        reply = run_codex_turn(
            input_text=input_text, workspace=workspace,
            resume=_codex_session_started,
            sandbox=str(codex_cfg.get("sandbox", "read-only")),
            timeout=float(codex_cfg.get("timeout", 120)),
            on_delta=on_delta, on_status=on_status)
        _codex_session_started = True
        return reply, "codex"

    # chat / gui / hermes 降级：本地直连（gui 追加 operate_gui 引导）
    text = input_text if route != "gui" else input_text + "\n" + GUI_NUDGE
    reply = run_local_run(
        endpoint=config.get("endpoint", ""), api_key=config.get("api_key", ""),
        model=config.get("model", ""), soul_md=soul_md,
        instructions=KURISU_OUTPUT_FORMAT, input_text=text,
        conversation_history=conversation_history, memories=memories,
        on_status=on_status, on_delta=on_delta, on_approval=on_approval)
    return reply, ("gui" if route == "gui" else "chat")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_backend_router.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add core/backend_router.py tests/test_backend_router.py
git commit -m "feat(router): gate 分流 + 三后端路由与降级链（agent-mode Task4）"
```

---

### Task 5: desktop_pet.py 接线 route_and_send

**Files:**
- Modify: `desktop_pet.py:258-259`（imports）、`desktop_pet.py:290-310`（AgentTask.run）

- [ ] **Step 1: 改 imports（L258-259）**

先 Grep 确认 `HERMES_DEFAULTS` 与 `KURISU_OUTPUT_FORMAT` 在 desktop_pet.py 其余处无引用（`Grep pattern=HERMES_DEFAULTS|KURISU_OUTPUT_FORMAT path=desktop_pet.py`）。若仅 AgentTask.run 使用，则把：

```python
    from config import HERMES_DEFAULTS, KURISU_OUTPUT_FORMAT, get_character_by_id, get_random_greeting
    from core.agent_client import _load_soul_md, run_local_run
```

改为：

```python
    from config import get_character_by_id, get_random_greeting
    from core.agent_client import _load_soul_md
```

（若 Grep 发现其他引用，保留对应名字，只删 `run_local_run`。）

- [ ] **Step 2: 改 AgentTask.run（原 L290-310）**

把 `try:` 块内的 `run_local_run(...)` 调用整体替换为：

```python
            try:
                from core.backend_router import route_and_send
                reply, _backend = route_and_send(
                    config=config,
                    input_text=self.history[-1]["content"],
                    soul_md=soul_md,
                    conversation_history=self.history[:-1],
                    memories=self.memories,
                    on_status=self.signals.status.emit,
                    on_delta=self.signals.delta.emit,
                    on_approval=self._handle_approval,
                )
                self.signals.finished.emit(reply)
            except Exception as exc:
                self.signals.failed.emit(str(exc))
```

- [ ] **Step 3: 语法检查 + 全量回归**

Run: `D:\anaconda\python.exe -m py_compile desktop_pet.py`
Expected: 无输出（exit 0）

Run: `D:\anaconda\python.exe -m pytest tests/ -q --tb=short`
Expected: 全部 passed（含此前 23 项 agent 测试 + 本计划新增测试）

- [ ] **Step 4: Commit**

```bash
git add desktop_pet.py
git commit -m "feat(pet): AgentTask 接入 route_and_send 三后端路由（agent-mode Task5）"
```

---

### Task 6: 设置页 Agent 模式 tab

**Files:**
- Modify: `ui/settings_dialog.py`（asr tab 之后、关于之前插 tab；新增 _probe_hermes；_save 写回）
- Test: `tests/test_settings_agent_tab.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_settings_agent_tab.py
"""设置页 Agent tab：加载默认值 + 保存写回（mock 完整罩住 load/save，防真实 config 被写）。"""
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication, QTabWidget


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_dialog(qapp, store):
    with patch("ui.settings_dialog.load_config", return_value=store), \
         patch("ui.settings_dialog.save_config"):
        from ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog()
    return dlg


def test_agent_tab_exists(qapp):
    dlg = _make_dialog(qapp, {})
    tabw = dlg.findChildren(QTabWidget)[0]
    names = [tabw.tabText(i) for i in range(tabw.count())]
    assert "Agent 模式" in names


def test_agent_tab_defaults(qapp):
    dlg = _make_dialog(qapp, {})
    assert dlg.agent_mode.currentData() == "chat"
    assert dlg.codex_sandbox.currentData() == "read-only"


def test_agent_tab_save(qapp):
    store = {}
    dlg = _make_dialog(qapp, store)
    dlg.agent_mode.setCurrentIndex(3)       # auto
    dlg.codex_sandbox.setCurrentIndex(1)    # workspace-write
    dlg.hermes_key.setText("hk")
    with patch("ui.settings_dialog.load_config", return_value=store), \
         patch("ui.settings_dialog.save_config") as save_mock:
        dlg._save()
    saved = save_mock.call_args.args[0]
    assert saved["agent_router"]["mode"] == "auto"
    assert saved["agent_router"]["codex"]["sandbox"] == "workspace-write"
    assert saved["hermes"]["api_key"] == "hk"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_settings_agent_tab.py -v`
Expected: FAIL `AttributeError: 'SettingsDialog' object has no attribute 'agent_mode'`

- [ ] **Step 3: 实现 tab（settings_dialog.py）**

3a. 在 `asr_form.addRow("ASR 模型", self.asr_model)` 与 `# === 关于 / 版本 ===` 之间插入：

```python
        # === Agent 模式（2026-08-15 agent-mode spec §4.4）===
        from config import AGENT_ROUTER_DEFAULTS, HERMES_DEFAULTS
        agent_page = QWidget()
        agent_form = QFormLayout(agent_page)
        router_cfg = {**AGENT_ROUTER_DEFAULTS, **(config.get("agent_router") or {})}
        self.agent_mode = QComboBox()
        self.agent_mode.addItem("本地直连（默认）", "chat")
        self.agent_mode.addItem("Hermes 网关（deepseek 模式）", "hermes")
        self.agent_mode.addItem("codex 子进程", "codex")
        self.agent_mode.addItem("自动分流（gate）", "auto")
        idx = self.agent_mode.findData(str(router_cfg.get("mode", "chat")))
        self.agent_mode.setCurrentIndex(max(idx, 0))
        agent_form.addRow("Agent 模式", self.agent_mode)

        hermes_cfg = {**HERMES_DEFAULTS, **(config.get("hermes") or {})}
        self.hermes_key = QLineEdit(str(hermes_cfg.get("api_key", "")))
        self.hermes_key.setEchoMode(QLineEdit.Password)
        agent_form.addRow("Hermes API Key", self.hermes_key)

        self.hermes_status = QLabel("未检测")
        self.hermes_status.setStyleSheet("color:#8a7f63")
        hermes_btn = QPushButton("检测 Hermes 网关")
        hermes_btn.clicked.connect(self._probe_hermes)
        agent_form.addRow(self.hermes_status, hermes_btn)

        codex_cfg = {**AGENT_ROUTER_DEFAULTS["codex"], **(router_cfg.get("codex") or {})}
        self.codex_sandbox = QComboBox()
        self.codex_sandbox.addItem("只读（默认）", "read-only")
        self.codex_sandbox.addItem("可写工作区", "workspace-write")
        idx = self.codex_sandbox.findData(str(codex_cfg.get("sandbox", "read-only")))
        self.codex_sandbox.setCurrentIndex(max(idx, 0))
        agent_form.addRow("codex 沙箱", self.codex_sandbox)
        tabs.addTab(agent_page, "Agent 模式")
```

3b. 在 `_check_update` 方法后新增方法：

```python
    def _probe_hermes(self) -> None:
        """同步探测 Hermes 网关 /health（2s 超时，设置页内可接受）。"""
        from config import HERMES_DEFAULTS
        from core.hermes_launcher import probe_health, read_profile_api_key
        hermes_cfg = {**HERMES_DEFAULTS, **(load_config().get("hermes") or {})}
        base_url = str(hermes_cfg.get("base_url") or "http://127.0.0.1:8642")
        api_key = str(hermes_cfg.get("api_key") or "") or (read_profile_api_key() or "")
        self.hermes_status.setText("检测中…")
        QApplication.processEvents()
        ok = probe_health(base_url, api_key)
        self.hermes_status.setText("在线" if ok else "离线")
        self.hermes_status.setStyleSheet("color:#34c759" if ok else "color:#d2738a")
```

3c. `_save` 中，`config.update({...})` 之后、`save_config(config)` 之前插入：

```python
        from config import AGENT_ROUTER_DEFAULTS, HERMES_DEFAULTS
        router_cfg = {**AGENT_ROUTER_DEFAULTS, **(config.get("agent_router") or {})}
        codex_cfg = {**AGENT_ROUTER_DEFAULTS["codex"], **(router_cfg.get("codex") or {})}
        codex_cfg["sandbox"] = self.codex_sandbox.currentData()
        config["agent_router"] = {"mode": self.agent_mode.currentData(), "codex": codex_cfg}
        hermes_cfg = {**HERMES_DEFAULTS, **(config.get("hermes") or {})}
        hermes_cfg["api_key"] = self.hermes_key.text().strip()
        config["hermes"] = hermes_cfg
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_settings_agent_tab.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add ui/settings_dialog.py tests/test_settings_agent_tab.py
git commit -m "feat(settings): Agent 模式 tab（模式/网关检测/codex 沙箱）（agent-mode Task6）"
```

---

### Task 7: 全量回归 + 手动验收

**Files:** 无新文件（验收 + lessons）

- [ ] **Step 1: 全量回归**

Run: `D:\anaconda\python.exe -m pytest tests/ -q --tb=short`
Expected: 全部 passed

- [ ] **Step 2: 手动验收（用户执行，前台 python 捕获 stderr——lessons 教训）**

启动：`D:\anaconda\python.exe desktop_pet.py`，逐项验证：

1. **默认行为不变**：不进设置页直接聊天 → 走本地直连，回复正常（回归）。
2. **Hermes 模式**：设置 → Agent 模式 → Hermes 网关 → 保存；发"帮我查一下今天天气"→ 首次应自动拉起网关（观察 `data/hermes_gateway.log` 生成）→ 回复带工具痕迹且语气符合红莉栖。若网关模型未配 DeepSeek，先跑 `hermes -p kurisu model` 配置。
3. **codex 模式**：切 codex 子进程 → 发两轮消息（第二轮验证 resume 上下文延续）→ `data/codex_workspace/AGENTS.md` 已生成且回复保持角色。
4. **auto 模式**：切自动分流 → 发"你好"（走 chat）、"搜一下 XX"（走 agent）、"打开记事本"（openclaw 未启用时降级 agent）。
5. **降级链**：停掉 hermes 网关（`hermes gateway stop` 或杀进程）+ 保持 hermes 模式发消息 → 气泡提示"已切本地直连"且回复正常。

- [ ] **Step 3: lessons 记录 + 最终提交**

在 `lessons.md` 追加"2026-08-15 Agent 模式实施"一节（5 条以内：实施中发现的真实教训，例如 codex JSONL 真实事件格式与 fixture 的差异、Hermes 网关拉起耗时实测等）。

```bash
git add lessons.md
git commit -m "docs(lessons): agent 模式实施教训（agent-mode Task7）"
```

---

## 自审记录（写计划后已核对）

1. **Spec 覆盖**：spec §3 架构→Task 4/5；§4.1 gate→Task 4；§4.2 Hermes→Task 2/4；§4.3 codex→Task 3/4；§4.4 配置/UI→Task 1/6；§6 降级链→Task 4 测试三例；§7 测试→各 Task + Task 7。无遗漏。
2. **占位符扫描**：无 TBD/TODO；Task 5 Step 1 的 Grep 条件分支是"防计划与现状脱节"的显式步骤，非占位符。
3. **类型一致性**：`route_and_send` 返回 `tuple[str, str]` 在 Task 4/5 一致；`run_codex_turn`/`ensure_gateway`/`probe_health` 签名在 Task 2/3/4 引用处一致；`_codex_session_started` 全局名一致；设置页属性名 `agent_mode/hermes_key/hermes_status/codex_sandbox` 在 Task 6 测试与实现一致。
