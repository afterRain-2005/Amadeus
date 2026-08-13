# Amadeus Agent 增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已有 function-calling agent 基础上补齐信息查询/文件管理工具缺口，接入 OpenClaw 重型 CUA 工具，并打磨响应速度体感。

**Architecture:** 不重建 agent loop（`run_local_run` 已是完整 function-calling 循环 + C 分级控制）。在 [core/desktop_tools.py](../../../core/desktop_tools.py) 扩 `TOOL_DEFINITIONS`/`execute_tool` 加 6 个工具 + 1 个 OpenClaw 工具；在 [config.py](../../../config.py) `APPROVAL_POLICY.auto_allow_tools` 登记只读工具；在 [desktop_pet.py](../../../desktop_pet.py) `_send` 改即时呼吸动画 + Live2D 倾听态；在 [core/agent_client.py](../../../core/agent_client.py) `_status_text` 增强进度文案。

**Tech Stack:** Python、`ddgs`（DuckDuckGo 搜索）、`trafilatura`（网页正文提取）、`httpx`、PySide6、pytest、OpenClaw（本地部署，待研究接口）

**上游 spec:** [2026-08-13-agent-enhancement-design.md](../specs/2026-08-13-agent-enhancement-design.md)

---

## File Structure

| 文件 | 责任 | 操作 |
|---|---|---|
| `core/desktop_tools.py` | 工具定义与执行 | 改：加 6 工具 + operate_gui + 路径校验 helper |
| `config.py` | 审批策略 | 改：`auto_allow_tools` 加 5 个只读工具 |
| `core/agent_client.py` | agent loop + 状态文案 | 改：`_status_text` 增强为带图标分段 |
| `desktop_pet.py` | 桌宠主窗口 | 改：`_send` 即时呼吸动画 + Live2D 倾听态 |
| `requirements.txt` | 依赖 | 改：加 `ddgs`、`trafilatura` |
| `tests/test_web_tools.py` | web_search/fetch_url 测试 | 新建 |
| `tests/test_file_tools.py` | 文件工具 + 路径校验测试 | 新建 |
| `tests/test_openclaw_tool.py` | operate_gui 降级测试 | 新建 |
| `tests/test_speed_polish.py` | _send 即时动画测试 | 新建 |
| `core/openclaw_runner.py` | OpenClaw 适配器 | 新建 |

---

## Task 1: 信息查询工具（web_search + fetch_url）

**Files:**
- Modify: `core/desktop_tools.py`（TOOL_DEFINITIONS + execute_tool）
- Modify: `config.py:51-56`（auto_allow_tools）
- Modify: `requirements.txt`
- Test: `tests/test_web_tools.py`

- [ ] **Step 1: 加依赖**

`requirements.txt` 末尾追加两行：
```
ddgs>=4.0
trafilatura>=1.12
```
Run: `python -m pip install ddgs trafilatura`

- [ ] **Step 2: 写失败测试**

`tests/test_web_tools.py`：
```python
"""web_search / fetch_url 工具测试。"""
from unittest.mock import patch, MagicMock


def test_web_search_returns_formatted_results():
    from core.desktop_tools import execute_tool
    fake_results = [
        {"title": "上海天气", "body": "今天晴 28度", "href": "https://example.com/1"},
    ]
    with patch("core.desktop_tools.DDGS") as mock_ddgs:
        instance = MagicMock()
        instance.text.return_value = fake_results
        mock_ddgs.return_value.__enter__.return_value = instance
        result = execute_tool("web_search", {"query": "上海天气"})
    assert "上海天气" in result["text"]
    assert "https://example.com/1" in result["text"]


def test_fetch_url_extracts_and_truncates():
    from core.desktop_tools import execute_tool
    with patch("core.desktop_tools.httpx_get_text") as mock_get, \
         patch("core.desktop_tools.trafilatura_extract") as mock_ext:
        mock_get.return_value = "<html>xx</html>"
        mock_ext.return_value = "正文" * 5000  # 10000 字符
        result = execute_tool("fetch_url", {"url": "https://example.com"})
    assert len(result["text"]) <= 8000
    assert "正文" in result["text"]


def test_fetch_url_rejects_non_http():
    from core.desktop_tools import execute_tool
    result = execute_tool("fetch_url", {"url": "file:///etc/passwd"})
    assert "failed" in result["text"].lower() or "不允许" in result["text"]
```

- [ ] **Step 3: 运行测试验证失败**

Run: `python -m pytest tests/test_web_tools.py -v`
Expected: FAIL（`DDGS`/`httpx_get_text`/`trafilatura_extract` 未定义）

- [ ] **Step 4: 在 desktop_tools.py 顶部加导入**

在 `core/desktop_tools.py` 现有导入后加：
```python
import httpx
from ddgs import DDGS
import trafilatura


def httpx_get_text(url: str, timeout: float = 15.0, max_bytes: int = 2_000_000) -> str:
    """抓取 URL 文本，限制响应体大小。"""
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            chunks = []
            size = 0
            for chunk in resp.iter_bytes(chunk_size=8192):
                size += len(chunk)
                if size > max_bytes:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", errors="replace")


def trafilatura_extract(html: str) -> str:
    """从 HTML 提取正文。"""
    return trafilatura.extract(html) or ""
```

- [ ] **Step 5: 在 TOOL_DEFINITIONS 加两个工具定义**

在 `TOOL_DEFINITIONS` 列表末尾（`run_command` 之后）加：
```python
    {"type": "function", "function": {"name": "web_search", "description": "Search the web with DuckDuckGo and return the top 5 results (title, snippet, url). Use for factual questions, current info, weather, etc.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "fetch_url", "description": "Fetch a web page and extract its main text content (up to 8000 chars). Use to read an article or page found via web_search. Only http/https URLs.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
```

- [ ] **Step 6: 在 execute_tool 加两个分支**

在 `execute_tool` 函数的 `if name == "run_command":` 分支前加：
```python
    if name == "web_search":
        query = arguments["query"].strip()
        if not query:
            return {"text": "Empty query."}
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=5))
        except Exception as exc:
            return {"text": f"Search failed: {exc}"}
        if not results:
            return {"text": "No results found."}
        lines = []
        for i, item in enumerate(results, 1):
            title = item.get("title", "")
            body = item.get("body", "")
            href = item.get("href", "")
            lines.append(f"{i}. {title}\n   {body}\n   {href}")
        return {"text": "\n".join(lines)}
    if name == "fetch_url":
        url = arguments["url"].strip()
        if not url.startswith(("http://", "https://")):
            return {"text": "Fetch failed: only http/https URLs are allowed."}
        try:
            html = httpx_get_text(url)
            text = trafilatura_extract(html)[:8000]
            return {"text": text or "No extractable content."}
        except Exception as exc:
            return {"text": f"Fetch failed: {exc}"}
```

- [ ] **Step 7: 更新 config.py auto_allow_tools**

在 `config.py` 的 `APPROVAL_POLICY["auto_allow_tools"]` 列表末尾加：
```python
        "web_search",        # 网页搜索（只读）
        "fetch_url",         # 抓取网页（只读）
```

- [ ] **Step 8: 运行测试验证通过**

Run: `python -m pytest tests/test_web_tools.py -v`
Expected: PASS

- [ ] **Step 9: 手动验证**

Run: `python desktop_pet.py`
发"帮我搜下今天上海天气"→ 应见搜索进度气泡 + 结果回复。

- [ ] **Step 10: Commit**

```bash
git add tests/test_web_tools.py core/desktop_tools.py config.py requirements.txt
git commit -m "feat: 信息查询工具 web_search + fetch_url（只读自动放行）"
```

---

## Task 2: 只读文件工具（file_find + list_dir + read_file）+ 路径校验

**Files:**
- Modify: `core/desktop_tools.py`（加路径校验 helper + 3 工具）
- Modify: `config.py`（auto_allow_tools 加 3 个）
- Test: `tests/test_file_tools.py`

- [ ] **Step 1: 写失败测试**

`tests/test_file_tools.py`：
```python
"""文件工具 + 路径校验测试。"""
import os
from pathlib import Path
import tempfile


def test_validate_path_rejects_traversal():
    from core.desktop_tools import _validate_path
    ok, _ = _validate_path("../../etc/passwd")
    assert ok is False


def test_validate_path_rejects_system_dir():
    from core.desktop_tools import _validate_path
    ok, _ = _validate_path("C:/Windows/System32/calc.exe")
    assert ok is False


def test_validate_path_accepts_user_file():
    from core.desktop_tools import _validate_path
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(b"hi")
        path = f.name
    try:
        ok, resolved = _validate_path(path)
        assert ok is True
        assert resolved.exists()
    finally:
        os.unlink(path)


def test_file_find_returns_matches():
    from core.desktop_tools import execute_tool, _validate_path
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "alpha.txt").write_text("x")
        (Path(d) / "beta.md").write_text("y")
        with __import__("unittest.mock").patch(
            "core.desktop_tools._default_search_root", return_value=Path(d)
        ):
            result = execute_tool("file_find", {"pattern": "*.txt", "root": d})
        assert "alpha.txt" in result["text"]
        assert "beta.md" not in result["text"]


def test_list_dir_lists_entries():
    from core.desktop_tools import execute_tool
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "a.txt").write_text("x")
        result = execute_tool("list_dir", {"path": d})
        assert "a.txt" in result["text"]


def test_read_file_returns_content():
    from core.desktop_tools import execute_tool
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8") as f:
        f.write("hello红莉栖")
        path = f.name
    try:
        result = execute_tool("read_file", {"path": path})
        assert "hello红莉栖" in result["text"]
    finally:
        os.unlink(path)


def test_read_file_rejects_traversal():
    from core.desktop_tools import execute_tool
    result = execute_tool("read_file", {"path": "../../../../etc/passwd"})
    assert "拒绝" in result["text"] or "denied" in result["text"].lower()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_file_tools.py -v`
Expected: FAIL（`_validate_path` 未定义）

- [ ] **Step 3: 加路径校验 helper**

在 `core/desktop_tools.py`（`httpx_get_text` 之前）加：
```python
# 允许的文件操作根目录（用户目录、桌面、项目根）
def _allowed_roots() -> list[Path]:
    home = Path.home().resolve()
    return [
        home,
        home / "Desktop",
        Path(DEFAULT_WORKDIR).resolve(),
    ]


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _validate_path(path: str) -> tuple[bool, Path]:
    """校验路径在允许根内且非系统目录。返回 (ok, resolved_path)。"""
    try:
        p = Path(os.path.expandvars(os.path.expanduser(path))).resolve()
    except (OSError, RuntimeError):
        return False, Path()
    # 拒绝系统目录
    sys_roots = [Path("C:/Windows"), Path("C:/Program Files"), Path("C:/Program Files (x86)")]
    for sr in sys_roots:
        if _is_under(p, sr):
            return False, p
    # 必须在允许根内
    for root in _allowed_roots():
        if _is_under(p, root):
            return True, p
    return False, p


def _default_search_root() -> Path:
    return Path.home() / "Desktop"
```

- [ ] **Step 4: 在 TOOL_DEFINITIONS 加 3 个工具定义**

在 `fetch_url` 定义后加：
```python
    {"type": "function", "function": {"name": "file_find", "description": "Find files matching a glob pattern (e.g. *.txt) under a root directory (defaults to Desktop). Returns up to 30 paths.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "root": {"type": "string", "description": "Directory to search under; defaults to user Desktop."}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "list_dir", "description": "List entries (name, size, type) in a directory. Returns up to 100 entries.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (up to 20000 chars, max 2MB). Rejects binary and paths outside allowed roots.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
```

- [ ] **Step 5: 在 execute_tool 加 3 个分支**

在 `fetch_url` 分支后加：
```python
    if name == "file_find":
        pattern = arguments["pattern"].strip() or "*"
        root_str = arguments.get("root") or str(_default_search_root())
        ok, root = _validate_path(root_str)
        if not ok or not root.is_dir():
            return {"text": "Search root denied or not a directory."}
        matches = sorted(root.rglob(pattern))[:30]
        if not matches:
            return {"text": "No files matched."}
        return {"text": "\n".join(str(m) for m in matches)}
    if name == "list_dir":
        ok, p = _validate_path(arguments["path"])
        if not ok or not p.is_dir():
            return {"text": "Directory denied or not found."}
        entries = []
        for child in sorted(p.iterdir())[:100]:
            kind = "DIR" if child.is_dir() else f"{child.stat().st_size}B"
            entries.append(f"{kind}\t{child.name}")
        return {"text": "\n".join(entries) or "Empty directory."}
    if name == "read_file":
        ok, p = _validate_path(arguments["path"])
        if not ok:
            return {"text": "Path denied: outside allowed roots or system directory."}
        if not p.is_file():
            return {"text": "Not a file."}
        if p.stat().st_size > 2_000_000:
            return {"text": "File too large (>2MB)."}
        try:
            text = p.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            return {"text": "Binary file, cannot read as text."}
        return {"text": text[:20000]}
```

- [ ] **Step 6: 更新 config.py auto_allow_tools**

在 `APPROVAL_POLICY["auto_allow_tools"]` 末尾加：
```python
        "file_find",         # 查找文件（只读）
        "list_dir",          # 列目录（只读）
        "read_file",         # 读文件（只读）
```

- [ ] **Step 7: 运行测试验证通过**

Run: `python -m pytest tests/test_file_tools.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add tests/test_file_tools.py core/desktop_tools.py config.py
git commit -m "feat: 只读文件工具 file_find/list_dir/read_file + 路径校验"
```

---

## Task 3: write_file 写文件工具

**Files:**
- Modify: `core/desktop_tools.py`（加 write_file + CONFIRMATION_REQUIRED）
- Test: `tests/test_file_tools.py`（追加）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_file_tools.py` 末尾加：
```python
def test_write_file_writes_content():
    from core.desktop_tools import execute_tool
    with tempfile.TemporaryDirectory() as d:
        target = str(Path(d) / "out.txt")
        result = execute_tool("write_file", {"path": target, "content": "新内容"})
        assert "written" in result["text"].lower() or "ok" in result["text"].lower()
        assert Path(target).read_text(encoding="utf-8") == "新内容"


def test_write_file_rejects_system_dir():
    from core.desktop_tools import execute_tool
    result = execute_tool("write_file", {"path": "C:/Windows/evil.bat", "content": "x"})
    assert "denied" in result["text"].lower() or "拒绝" in result["text"]
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_file_tools.py::test_write_file_writes_content -v`
Expected: FAIL（`write_file` 未实现）

- [ ] **Step 3: 在 TOOL_DEFINITIONS 加 write_file 定义**

在 `read_file` 定义后加：
```python
    {"type": "function", "function": {"name": "write_file", "description": "Write text content to a file (overwrites). Path must be inside allowed roots. Requires user confirmation.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
```

- [ ] **Step 4: 在 execute_tool 加 write_file 分支**

在 `read_file` 分支后加：
```python
    if name == "write_file":
        ok, p = _validate_path(arguments["path"])
        if not ok:
            return {"text": "Write denied: path outside allowed roots or system directory."}
        content = arguments.get("content", "")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except OSError as exc:
            return {"text": f"Write failed: {exc}"}
        return {"text": f"Written {len(content)} chars to {p}."}
```

- [ ] **Step 5: 把 write_file 加入 CONFIRMATION_REQUIRED**

修改 `core/desktop_tools.py` 的 `CONFIRMATION_REQUIRED`：
```python
CONFIRMATION_REQUIRED = {"open_target", "type_text", "press_keys", "click", "run_command", "write_file"}
```

- [ ] **Step 6: 运行测试验证通过**

Run: `python -m pytest tests/test_file_tools.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add tests/test_file_tools.py core/desktop_tools.py
git commit -m "feat: write_file 写文件工具（需确认 + 路径校验）"
```

---

## Task 4: 响应速度——呼吸动画贯穿等待 + Live2D 即时倾听态

**Files:**
- Modify: `desktop_pet.py:803`（`_send` 的静态"让我想想…"）
- Modify: `desktop_pet.py`（`_send` 发 emotion）
- Test: `tests/test_speed_polish.py`

- [ ] **Step 1: 写失败测试**

`tests/test_speed_polish.py`：
```python
"""_send 即时反应测试：发送瞬间触发呼吸动画 + emotion，不等 delta。"""
from unittest.mock import MagicMock, patch


def test_send_triggers_thinking_dots_immediately():
    """_send 应立即调用 _show_thinking_dots，而非设静态'让我想想…'。"""
    from desktop_pet import _decide_send_instant_action
    action = _decide_send_instant_action()
    assert action["show_thinking_dots"] is True
    assert action["emotion"] == "thinking"


def test_send_does_not_use_static_let_me_think():
    """不应再返回静态文本'让我想想…'。"""
    from desktop_pet import _decide_send_instant_action
    action = _decide_send_instant_action()
    assert action.get("static_text") != "让我想想…"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_speed_polish.py -v`
Expected: FAIL（`_decide_send_instant_action` 未定义）

- [ ] **Step 3: 加模块级决策函数**

在 `desktop_pet.py` 的 `_decide_delta_action` 函数附近加：
```python
def _decide_send_instant_action() -> dict:
    """_send 发送瞬间的即时反应决策：呼吸动画 + thinking emotion。
    返回 {show_thinking_dots, emotion}。不再用静态'让我想想…'。"""
    return {"show_thinking_dots": True, "emotion": "thinking"}
```

- [ ] **Step 4: 修改 _send 用即时呼吸动画**

找到 `desktop_pet.py` 中 `self._set_bubble_text("让我想想…")`（约 803 行），替换为：
```python
            instant = _decide_send_instant_action()
            self._show_thinking_dots()
            self._send_emotion(instant["emotion"])
```

- [ ] **Step 5: 加 _send_emotion 方法**

在 PetWindow 内（`_show_thinking_dots` 附近）加：
```python
        def _send_emotion(self, emotion: str) -> None:
            """经 pet_command.json 发送 emotion 到 renderer（即时，不等 LLM）。"""
            import json, time
            try:
                cmd = {"timestamp": time.time(), "emotion": emotion}
                COMMAND_FILE.write_text(json.dumps(cmd), encoding="utf-8")
            except OSError:
                pass
```

- [ ] **Step 6: 运行测试验证通过**

Run: `python -m pytest tests/test_speed_polish.py -v`
Expected: PASS

- [ ] **Step 7: 手动验证**

Run: `python desktop_pet.py`
发消息→观察：发送瞬间角色切思考态 + "● ● ●"呼吸（不再静态"让我想想…"），LLM 回复后平滑过渡。

- [ ] **Step 8: Commit**

```bash
git add tests/test_speed_polish.py desktop_pet.py
git commit -m "feat: 响应速度体感（即时呼吸动画 + Live2D 倾听态替代静态文本）"
```

---

## Task 5: 工具进度文案增强（带图标分段）

**Files:**
- Modify: `core/agent_client.py:90-95`（`_status_text`）
- Test: `tests/test_status_text.py`

- [ ] **Step 1: 写失败测试**

`tests/test_status_text.py`：
```python
"""_status_text 带图标分段文案测试。"""


def test_status_text_web_search_has_icon():
    from core.agent_client import _status_text
    text = _status_text("web_search", {"query": "上海天气"})
    assert "🔍" in text
    assert "上海天气" in text


def test_status_text_read_file_has_icon():
    from core.agent_client import _status_text
    text = _status_text("read_file", {"path": "C:/x.txt"})
    assert "📄" in text


def test_status_text_operate_gui_has_icon():
    from core.agent_client import _status_text
    text = _status_text("operate_gui", {"task": "打开浏览器"})
    assert "🖱" in text or "gui" in text.lower()


def test_status_text_unknown_tool_fallback():
    from core.agent_client import _status_text
    text = _status_text("unknown_tool", {})
    assert "正在" in text
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_status_text.py -v`
Expected: FAIL（`web_search`/`read_file`/`operate_gui` 无图标文案）

- [ ] **Step 3: 重写 _status_text**

替换 `core/agent_client.py` 的 `_status_text` 函数为：
```python
def _status_text(name: str, arguments: dict) -> str:
    """工具执行进度文案，带图标 + 关键参数。"""
    if name == "web_search":
        return f"🔍 搜索：{arguments.get('query', '')}"
    if name == "fetch_url":
        return f"🌐 读取网页：{arguments.get('url', '')}"
    if name == "file_find":
        return f"🔎 查找文件：{arguments.get('pattern', '')}"
    if name == "list_dir":
        return f"📁 列目录：{arguments.get('path', '')}"
    if name == "read_file":
        return f"📄 读取：{arguments.get('path', '')}"
    if name == "write_file":
        return f"✏️ 写入：{arguments.get('path', '')}"
    if name == "operate_gui":
        return f"🖱 操作 GUI：{arguments.get('task', '')}"
    labels = {"capture_screen": "🖥 正在观察屏幕…", "list_windows": "🪟 正在查看窗口…",
              "read_clipboard": "📋 正在读取剪贴板…", "open_target": "📂 准备打开目标…",
              "focus_window": "🪟 正在切换窗口…", "type_text": "⌨️ 准备输入文字…",
              "press_keys": "⌨️ 准备按下快捷键…", "click": "🖱 准备点击…",
              "run_command": "⚙️ 准备执行命令…"}
    return labels.get(name, f"正在执行 {name}…")
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_status_text.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_status_text.py core/agent_client.py
git commit -m "feat: 工具进度文案带图标分段（搜索/读取/GUI 等）"
```

---

## Task 6: OpenClaw operate_gui 重型 CUA 工具

**Files:**
- Create: `core/openclaw_runner.py`
- Modify: `core/desktop_tools.py`（加 operate_gui + CONFIRMATION_REQUIRED）
- Test: `tests/test_openclaw_tool.py`

- [ ] **Step 1: 研究 OpenClaw 调用接口**

Run: 用 WebFetch 抓 OpenClaw 官方仓库 README（https://github.com/steipete/OpenClaw 或搜索得到的官方地址），确认其本地调用方式（CLI 命令 / Python SDK / HTTP API）。
记录：调用命令格式、输入参数（task 描述）、输出（结果文本/截图）、是否支持超时/中断。
若仓库地址不确定，用 WebSearch "OpenClaw github 本地调用 CLI" 找到后抓取。

- [ ] **Step 2: 写失败测试**

`tests/test_openclaw_tool.py`：
```python
"""operate_gui 工具测试：降级 + 适配器契约。"""
from unittest.mock import patch


def test_operate_gui_returns_error_when_openclaw_not_deployed():
    from core.desktop_tools import execute_tool
    with patch("core.openclaw_runner.is_openclaw_available", return_value=False):
        result = execute_tool("operate_gui", {"task": "打开记事本写一句话"})
    assert "未部署" in result["text"] or "not" in result["text"].lower()


def test_operate_gui_runs_when_available():
    from core.desktop_tools import execute_tool
    with patch("core.openclaw_runner.is_openclaw_available", return_value=True), \
         patch("core.openclaw_runner.run_openclaw", return_value="已完成：打开记事本"):
        result = execute_tool("operate_gui", {"task": "打开记事本写一句话"})
    assert "已完成" in result["text"]


def test_operate_gui_timeout_returns_error():
    from core.desktop_tools import execute_tool
    with patch("core.openclaw_runner.is_openclaw_available", return_value=True), \
         patch("core.openclaw_runner.run_openclaw", side_effect=TimeoutError("120s")):
        result = execute_tool("operate_gui", {"task": "复杂任务"})
    assert "超时" in result["text"] or "timeout" in result["text"].lower()
```

- [ ] **Step 3: 运行测试验证失败**

Run: `python -m pytest tests/test_openclaw_tool.py -v`
Expected: FAIL（`core.openclaw_runner` 不存在）

- [ ] **Step 4: 创建 openclaw_runner.py 适配器**

`core/openclaw_runner.py`：
```python
"""OpenClaw 适配器：封装 OpenClaw 本地调用。

调用接口在 Task 6 Step 1 研究后填入 run_openclaw。
is_openclaw_available 检测本地是否部署 OpenClaw。
"""
from __future__ import annotations

import shutil
from pathlib import Path


def is_openclaw_available() -> bool:
    """检测 OpenClaw 是否本地可用。优先查 CLI 是否在 PATH，其次查常见安装目录。"""
    if shutil.which("openclaw"):
        return True
    candidates = [Path.home() / ".openclaw", Path.home() / "AppData/Local/OpenClaw"]
    return any(p.exists() for p in candidates)


def run_openclaw(task: str, timeout: int = 120) -> str:
    """执行一个 OpenClaw 视觉 GUI 任务，返回结果摘要。

    实现依 Task 6 Step 1 研究结果：若 OpenClaw 提供 CLI，用 subprocess 调用；
    若提供 Python SDK，import 后调用；若提供 HTTP API，用 httpx 调用。
    超时抛 TimeoutError。
    """
    # TODO（Step 1 研究后填入）：根据 OpenClaw 实际接口实现
    raise NotImplementedError(
        "OpenClaw 调用实现待 Step 1 研究后填入。"
        "可用 is_openclaw_available() 先判可用性。"
    )
```

- [ ] **Step 5: 在 TOOL_DEFINITIONS 加 operate_gui 定义**

在 `write_file` 定义后加：
```python
    {"type": "function", "function": {"name": "operate_gui", "description": "Operate the computer GUI visually via OpenClaw for complex multi-step tasks (fill forms, click through menus, drive apps without CLI). Use only when simpler tools (click/type_text/focus_window) are insufficient. Requires OpenClaw locally deployed. Requires user confirmation.", "parameters": {"type": "object", "properties": {"task": {"type": "string", "description": "Natural language description of the GUI task to perform."}}, "required": ["task"]}}},
```

- [ ] **Step 6: 在 execute_tool 加 operate_gui 分支**

在 `write_file` 分支后加：
```python
    if name == "operate_gui":
        from core.openclaw_runner import is_openclaw_available, run_openclaw
        task = arguments["task"].strip()
        if not is_openclaw_available():
            return {"text": "OpenClaw 未部署，无法执行视觉操作。请改用 click/type_text/focus_window 或先部署 OpenClaw。"}
        try:
            summary = run_openclaw(task, timeout=120)
            return {"text": summary}
        except TimeoutError:
            return {"text": "OpenClaw 操作超时（120s）。"}
        except NotImplementedError as exc:
            return {"text": f"OpenClaw 调用未实现：{exc}"}
        except Exception as exc:
            return {"text": f"OpenClaw 操作失败：{exc}"}
```

- [ ] **Step 7: 把 operate_gui 加入 CONFIRMATION_REQUIRED**

修改 `CONFIRMATION_REQUIRED`：
```python
CONFIRMATION_REQUIRED = {"open_target", "type_text", "press_keys", "click", "run_command", "write_file", "operate_gui"}
```

- [ ] **Step 8: 运行测试验证通过**

Run: `python -m pytest tests/test_openclaw_tool.py -v`
Expected: PASS（降级 + 可用路径 mock，run_openclaw 实现留 Step 1 研究后填）

- [ ] **Step 9: 填入 run_openclaw 实现（依 Step 1 研究）**

根据 Step 1 抓取的 OpenClaw 接口，实现 `run_openclaw`：
- 若 CLI：`subprocess.run(["openclaw", "run", "--task", task], capture_output=True, timeout=120)`
- 若 SDK：`import openclaw; openclaw.run(task, timeout=120)`
- 若 HTTP：`httpx.post("http://127.0.0.1:<port>/run", json={"task": task}, timeout=120)`
解析输出为结果摘要文本返回。

- [ ] **Step 10: 手动验证（需 OpenClaw 已部署）**

Run: `python desktop_pet.py`
发"打开浏览器登录 GitHub"→ 见确认弹窗 → 同意 → 见"🖱 操作 GUI"进度 → 见结果。
若 OpenClaw 未部署，验证降级提示。

- [ ] **Step 11: Commit**

```bash
git add tests/test_openclaw_tool.py core/openclaw_runner.py core/desktop_tools.py
git commit -m "feat: OpenClaw operate_gui 重型 CUA 工具（含降级 + 适配器）"
```

---

## Self-Review

**Spec 覆盖检查**：
- §3.1 web_search/fetch_url → Task 1 ✓
- §3.2 file_find/list_dir/read_file → Task 2 ✓
- §3.2 write_file → Task 3 ✓
- §3.3 风险分级落点（auto_allow + CONFIRMATION）→ Task 1/2/3/6 各自 Step ✓
- §4 OpenClaw operate_gui → Task 6 ✓
- §5.1 Live2D 即时倾听态 → Task 4 ✓
- §5.2 呼吸动画贯穿 → Task 4 ✓
- §5.3 工具进度分段反馈 → Task 5 ✓
- §6 安全（路径校验/大小/超时）→ Task 2/3/6 ✓

**Placeholder 扫描**：Task 6 Step 9 的 run_openclaw 实现依赖 Step 1 研究结果，已在 Step 1 明确研究动作 + Step 9 给出三种接口的填充模板，非空占位。其余步骤均有完整代码。

**类型一致性**：`_validate_path` 返回 `tuple[bool, Path]`，Task 2/3 调用一致；`_decide_send_instant_action` 返回 dict，Task 4 调用一致；`_status_text(name, arguments)` 签名 Task 5 与现有一致；`is_openclaw_available`/`run_openclaw` 在 openclaw_runner 定义、desktop_tools 调用一致。

**已知简化**：OpenClaw 实际接口待研究填入（Task 6 Step 1/9），spec §9 已声明此待定。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-13-agent-enhancement.md`. Two execution options:

**1. Subagent-Driven (recommended)** - 每个 Task 派独立 subagent，任务间审查，快速迭代

**2. Inline Execution** - 当前会话内执行，批量执行 + 检查点
