# Amadeus P0 工程治理 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清除 amadeus-py 的"玩具"工程味——删死代码、只留红莉栖并去登录、文件轮询 IPC 改为进程内双向管道、设置页加后端默认标注与关于页、规范化打包、引入版本号与版本提示。

**Architecture:** 保留 PySide6 + pywebview + Live2D 双进程骨架（overlay 主进程 + renderer 渲染子进程），但把 `mp.Pipe(duplex=False)` 改为 `duplex=True`，使 emotion/speaking 命令走管道反向通道，删除 `pet_command.json` 文件轮询。删除多角色与登录，main.py 启动直接进红莉栖桌宠。

**Tech Stack:** Python 3.11+, PySide6, pywebview, pywin32, PyInstaller, pytest（新增）

**对应 Spec：** [docs/superpowers/specs/2026-08-13-amadeus-productization-design.md](../specs/2026-08-13-amadeus-productization-design.md) 第 4 节 P0 行 + 第 7 节。

**TDD 说明：** 本项目无既有测试。逻辑代码（版本检查、命令序列化）走严格 TDD；删除与 UI/进程布线类改动用"运行验证"步骤（启动应用、观察行为）替代，因这类改动无法用单元测试有意义地覆盖。每步均给出可执行的验证命令与期望结果。

---

## 文件结构

**删除：**
- `ui/chat_window.py` — 死代码（[lessons.md](../../../lessons.md) 教训3 已标 deprecated）
- `ui/companion_window.py` — 死代码
- `core/codex_bridge.py` — 死代码
- `core/hermes_process.py` — 死代码
- `ui/login_window.py` — 登录移除后无用
- `core/auth.py` — 仅 login_window 使用
- `core/pet_controller.py` — IPC 重构后 `pet_command.json` 机制无用

**新建：**
- `core/version.py` — `__version__` 与 `check_latest_version()`
- `tests/__init__.py`、`tests/test_version.py`、`tests/test_ipc_command.py` — 测试
- `docs/build.md` — 打包说明（用户规则：不主动建文档，但打包流程需文档化供执行者参考；此为计划内必要产物）

**修改：**
- `config.py` — 移除真帆/真由理，CHARACTERS 只留红莉栖，Character 去账号密码字段，去 find_character_by_login
- `main.py` — 移除登录流程，启动直接进桌宠
- `desktop_pet.py` — IPC 改 duplex=True，命令走管道，删 COMMAND_FILE
- `ui/settings_dialog.py` — 后端 tab 标注默认、新增"关于"tab
- `Amadeus.spec` — 引入版本号
- `requirements.txt` — 加 pytest

---

## Task 1: 引入版本号与版本检查（TDD）

**Files:**
- Create: `core/version.py`
- Create: `tests/__init__.py`
- Create: `tests/test_version.py`
- Modify: `requirements.txt`

- [ ] **Step 1: 在 requirements.txt 加测试依赖**

修改 `requirements.txt`，末尾追加：

```
pytest>=8.0
```

- [ ] **Step 2: 安装依赖**

Run: `python -m pip install -r requirements.txt`
Expected: 安装成功，`pytest` 可用。

- [ ] **Step 3: 创建 tests 包**

创建 `tests/__init__.py`，内容为空。

- [ ] **Step 4: 写失败测试**

创建 `tests/test_version.py`：

```python
from unittest.mock import patch, MagicMock
from core.version import __version__, check_latest_version, parse_version


def test_version_is_string():
    assert isinstance(__version__, str)
    assert __version__.count(".") == 2  # 形如 0.2.0


def test_parse_version():
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("0.0.10") == (0, 0, 10)


def test_check_latest_version_no_url_returns_none():
    assert check_latest_version("") is None
    assert check_latest_version(None) is None


def test_check_latest_version_fetches_plain_text():
    fake_resp = MagicMock()
    fake_resp.read.return_value = b"0.9.0\n"
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda *a: None
    with patch("core.version.urllib.request.urlopen", return_value=fake_resp) as m:
        result = check_latest_version("https://example.com/version.txt")
    m.assert_called_once_with("https://example.com/version.txt", timeout=5)
    assert result == "0.9.0"


def test_check_latest_version_network_error_returns_none():
    with patch("core.version.urllib.request.urlopen", side_effect=OSError("timeout")):
        assert check_latest_version("https://example.com/version.txt") is None
```

- [ ] **Step 5: 运行测试确认失败**

Run: `python -m pytest tests/test_version.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.version'`

- [ ] **Step 6: 实现 core/version.py**

创建 `core/version.py`：

```python
"""应用版本号与远程版本检查。

版本检查采用“拉取纯文本版本字符串”策略，URL 可在设置页配置。
未配置 URL 时跳过检查（自用场景默认无远程源）。
"""
from __future__ import annotations

import urllib.request
from typing import Optional

__version__ = "0.2.0"


def parse_version(text: str) -> tuple[int, int, int]:
    """把 '0.2.0' 解析为 (0, 2, 0)。非法格式抛 ValueError。"""
    parts = text.strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"非法版本号：{text!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def check_latest_version(url: Optional[str]) -> Optional[str]:
    """从 url 拉取最新版本字符串（纯文本，首行）。

    无 url 或网络失败时返回 None，绝不抛异常。
    """
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        return text.splitlines()[0].strip() if text.strip() else None
    except (OSError, ValueError, IndexError):
        return None
```

- [ ] **Step 7: 运行测试确认通过**

Run: `python -m pytest tests/test_version.py -v`
Expected: PASS（5 项全过）

- [ ] **Step 8: 提交**

Run:
```
git add core/version.py tests/__init__.py tests/test_version.py requirements.txt
git commit -m "feat: 引入版本号与远程版本检查（P0）"
```

---

## Task 2: 删除死代码

**Files:**
- Delete: `ui/chat_window.py`, `ui/companion_window.py`, `core/codex_bridge.py`, `core/hermes_process.py`
- Modify: `core/agent_client.py`（移除 `run_direct_agent`）, `main.py`（移除 `_on_chat_logout`）

- [ ] **Step 1: 确认死代码无引用**

Run:
```
python -c "import ast,sys; [print(p) for p in ['ui/chat_window.py','ui/companion_window.py','core/codex_bridge.py','core/hermes_process.py']]"
```
然后用 Grep 工具搜索 `chat_window|companion_window|codex_bridge|hermes_process` 在所有 `.py` 中的 import。
Expected: 除文件自身外无 import 引用（已确认 main.py 不再 import ChatWindow，desktop_pet.py 不 import 这些）。

- [ ] **Step 2: 删除四个死代码文件**

用 DeleteFile 工具删除：
- `d:\Desktop\Ideas\Amadeus2026\amadeus-py\ui\chat_window.py`
- `d:\Desktop\Ideas\Amadeus2026\amadeus-py\ui\companion_window.py`
- `d:\Desktop\Ideas\Amadeus2026\amadeus-py\core\codex_bridge.py`
- `d:\Desktop\Ideas\Amadeus2026\amadeus-py\core\hermes_process.py`

- [ ] **Step 3: 移除 agent_client.run_direct_agent**

在 `core/agent_client.py` 中删除 `run_direct_agent` 函数整体（从 `def run_direct_agent(` 到该函数结束）。该函数已标 `[deprecated]` 且无调用方。

删除后用 Grep 确认无 `run_direct_agent` 残留引用：
Run（用 Grep 工具）: pattern `run_direct_agent`
Expected: 无匹配。

- [ ] **Step 4: 移除 main._on_chat_logout**

在 `main.py` 中删除 `_on_chat_logout` 函数整体（已标 `[deprecated]`，无信号 connect）。

- [ ] **Step 5: 验证可导入**

Run: `python -c "import main; import core.agent_client; print('ok')"`
Expected: 输出 `ok`，无 ImportError。

- [ ] **Step 6: 提交**

Run:
```
git add -A
git commit -m "chore: 删除死代码（chat_window/companion_window/codex_bridge/hermes_process/run_direct_agent/_on_chat_logout）"
```

---

## Task 3: 移除多角色，只留红莉栖，并去登录

**Files:**
- Delete: `ui/login_window.py`, `core/auth.py`
- Modify: `config.py`, `main.py`

- [ ] **Step 1: 精简 config.py——删除真帆/真由理**

在 `config.py` 中删除以下常量整体：
- `MAHO_PERSONALITY`（约 182-250 行）
- `MAHO_GREETINGS`（约 252-257 行）
- `MAY_PERSONALITY`（约 260-307 行）
- `MAY_GREETINGS`（约 309-314 行）

- [ ] **Step 2: Character 去账号密码字段**

把 `Character` dataclass 中的 `account: str` 与 `password: str` 两行删除（登录移除后无用）。

- [ ] **Step 3: CHARACTERS 只留红莉栖，去掉账号密码**

把 `CHARACTERS` 列表替换为：

```python
CHARACTERS: list[Character] = [
    Character(
        id="kurisu",
        name="牧濑红莉栖",
        live2d_path="/live2d/kurisu/amadeusV1.model3.json",
        bg_image="/bg.png",
        bg_login_image="/bgLogin.jpg",
        bgm="/login.mp3",
        sprite_logo="/sprite_logo.png",
        voice_sample="/voice_sample.mp3",
        personality=KURISU_PERSONALITY,
        greetings=KURISU_GREETINGS,
    ),
]
```

- [ ] **Step 4: 删除 find_character_by_login**

删除 `find_character_by_login` 函数整体（登录移除后无用）。保留 `get_character_by_id`、`get_random_greeting`、`DEFAULT_CHARACTER`。

- [ ] **Step 5: 删除 login_window.py 与 core/auth.py**

用 DeleteFile 工具删除：
- `d:\Desktop\Ideas\Amadeus2026\amadeus-py\ui\login_window.py`
- `d:\Desktop\Ideas\Amadeus2026\amadeus-py\core\auth.py`

- [ ] **Step 6: 重写 main.py——去登录，直接起桌宠**

把 `main.py` 整体替换为：

```python
"""Amadeus desktop agent entry point.

P0 起：移除登录流程，启动直接进入红莉栖桌宠。
冻结模式下 exe 用 --desktop-pet 自调用拉起桌宠子进程；开发模式下用 desktop_pet.py。
"""
from __future__ import annotations

import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon


# 全局引用，防止 gc 回收
_tray_icon: QSystemTrayIcon | None = None
_pet_process: subprocess.Popen | None = None


def _is_frozen() -> bool:
    return getattr(sys, 'frozen', False)


def _resource_dir() -> Path:
    if _is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _start_pet_process() -> None:
    """启动桌宠进程。"""
    global _pet_process
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    if _is_frozen():
        _pet_process = subprocess.Popen(
            [sys.executable, "--desktop-pet"],
            creationflags=creation_flags,
        )
    else:
        _pet_process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve().parent / "desktop_pet.py")],
            cwd=str(Path(__file__).resolve().parent),
            creationflags=creation_flags,
        )


def main() -> int:
    global _tray_icon
    print("[main] 启动 QApplication...", flush=True)
    app = QApplication(sys.argv)
    app.setApplicationName("Amadeus")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(str(_resource_dir() / "resources" / "Kurisu.png")))

    # 直接起桌宠，无登录
    _start_pet_process()

    _tray_icon = QSystemTrayIcon(app.windowIcon(), app)
    tray_menu = QMenu()
    tray_menu.addAction("显示窗口", _show_windows)
    tray_menu.addAction("退出", _on_quit)
    _tray_icon.setContextMenu(tray_menu)
    _tray_icon.activated.connect(
        lambda reason: _show_windows()
        if reason == QSystemTrayIcon.ActivationReason.Trigger else None
    )
    _tray_icon.show()
    print("[main] 桌宠进程已启动，进入事件循环...", flush=True)
    return app.exec()


def _on_quit() -> None:
    global _pet_process
    print("[main] 退出程序", flush=True)
    if _pet_process is not None and _pet_process.poll() is None:
        _pet_process.terminate()
    QApplication.quit()


def _show_windows() -> None:
    global _pet_process
    if _pet_process is None or _pet_process.poll() is not None:
        _start_pet_process()


if __name__ == "__main__":
    if "--desktop-pet" in sys.argv:
        from desktop_pet import main as pet_main
        sys.exit(pet_main())
    sys.exit(main())
```

- [ ] **Step 7: 验证导入与启动**

Run: `python -c "import main; from config import CHARACTERS, get_character_by_id; assert len(CHARACTERS)==1 and CHARACTERS[0].id=='kurisu'; print('ok')"`
Expected: 输出 `ok`。

- [ ] **Step 8: 手动验证——启动应用**

Run（在项目根目录，非阻塞）: `python main.py`
Expected: 登录窗口不再出现；红莉栖桌宠窗口直接显示在屏幕右下角，Live2D 正常渲染，无异常崩窗。手动关闭后继续。

- [ ] **Step 9: 提交**

Run:
```
git add -A
git commit -m "feat: 移除多角色与登录，启动直接进红莉栖桌宠（P0）"
```

---

## Task 4: 设置页——后端默认标注 + 关于页

**Files:**
- Modify: `ui/settings_dialog.py`

- [ ] **Step 1: 修改"Chat 模型"tab 标题与提示，标注为默认**

在 `ui/settings_dialog.py` 中，把 `tabs.addTab(model_page, "Chat 模型")` 改为：

```python
        tabs.addTab(model_page, "直连模型（默认）")
```

并在 `model_form` 顶部加一行说明（在 `model_form = QFormLayout(model_page)` 之后插入）：

```python
        model_form.addRow(QLabel("默认后端：直连 OpenAI 兼容 API。Hermes 为可选高级模式，见另一 tab。"))
```

- [ ] **Step 2: 修改"Hermes 后端"tab 标题，标注为可选**

把 `tabs.addTab(hermes_page, "Hermes 后端")` 改为：

```python
        tabs.addTab(hermes_page, "Hermes 后端（可选）")
```

- [ ] **Step 3: 新增"关于"tab——版本号与版本检查**

在 `buttons = QDialogButtonBox(...)` 之前插入新 tab：

```python
        # === 关于 / 版本 ===
        from core.version import __version__, check_latest_version
        about_page = QWidget()
        about_form = QFormLayout(about_page)
        about_form.addRow("当前版本", QLabel(__version__))
        self.version_check_url = QLineEdit(config.get("version_check_url", ""))
        self.version_check_url.setPlaceholderText("远程版本检查 URL（纯文本，可留空）")
        about_form.addRow("版本检查 URL", self.version_check_url)
        self.version_status = QLabel("未检查")
        self.version_status.setStyleSheet("color:#8e8e93")
        about_form.addRow("最新版本", self.version_status)
        check_btn = QPushButton("检查更新")
        check_btn.clicked.connect(self._check_update)
        about_form.addRow(check_btn)
        tabs.addTab(about_page, "关于")
```

- [ ] **Step 4: 实现 _check_update 方法**

在 `_run_setup_script` 方法之前插入：

```python
    def _check_update(self) -> None:
        from core.version import __version__, check_latest_version, parse_version
        url = self.version_check_url.text().strip()
        self.version_status.setText("检查中…")
        QApplication.processEvents()
        latest = check_latest_version(url)
        if latest is None:
            self.version_status.setText("未配置 URL 或检查失败")
            self.version_status.setStyleSheet("color:#8e8e93")
            return
        try:
            if parse_version(latest) > parse_version(__version__):
                self.version_status.setText(f"{latest}（有新版）")
                self.version_status.setStyleSheet("color:#ff3b30")
            else:
                self.version_status.setText(f"{latest}（已是最新）")
                self.version_status.setStyleSheet("color:#34c759")
        except ValueError:
            self.version_status.setText(f"{latest}（版本号格式异常）")
            self.version_status.setStyleSheet("color:#8e8e93")
```

并在文件顶部 import 块补 `QApplication`：

```python
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QTabWidget,
    QVBoxLayout, QWidget,
)
```

- [ ] **Step 5: 在 _save 中持久化 version_check_url**

在 `_save` 的 `config.update({...})` 字典里追加一个键：

```python
            "version_check_url": self.version_check_url.text().strip(),
```

- [ ] **Step 6: 手动验证——打开设置页**

Run（非阻塞）: `python main.py`，点击桌宠工具栏 ⚙ 打开设置。
Expected: tab 列表含"直连模型（默认）""Hermes 后端（可选）""审批策略""语音合成""语音输入""关于"。"关于"页显示当前版本 `0.2.0`，URL 为空时点"检查更新"显示"未配置 URL 或检查失败"。

- [ ] **Step 7: 提交**

Run:
```
git add ui/settings_dialog.py
git commit -m "feat: 设置页标注默认后端 + 新增关于/版本检查 tab（P0）"
```

---

## Task 5: IPC 重构——文件轮询改双向管道

**Files:**
- Modify: `desktop_pet.py`
- Create: `tests/test_ipc_command.py`
- Delete: `core/pet_controller.py`

- [ ] **Step 1: 写命令序列化测试**

创建 `tests/test_ipc_command.py`：

```python
from desktop_pet import serialize_command, apply_command_js


def test_serialize_command_emotion():
    assert serialize_command(emotion="smile") == ("command", {"emotion": "smile"})


def test_serialize_command_speaking():
    assert serialize_command(speaking=True) == ("command", {"speaking": True})


def test_serialize_command_multi():
    out = serialize_command(emotion="angry", speaking=False)
    assert out[0] == "command"
    assert out[1] == {"emotion": "angry", "speaking": False}


def test_apply_command_js_emotion():
    assert apply_command_js({"emotion": "blush"}) == "window.__amadeus.setEmotion('blush')"


def test_apply_command_js_speaking_true():
    assert apply_command_js({"speaking": True}) == "window.__amadeus.setSpeaking(true)"


def test_apply_command_js_speaking_false():
    assert apply_command_js({"speaking": False}) == "window.__amadeus.setSpeaking(false)"


def test_apply_command_js_both():
    js = apply_command_js({"emotion": "smile", "speaking": True})
    assert "setEmotion('smile')" in js
    assert "setSpeaking(true)" in js


def test_apply_command_js_empty():
    assert apply_command_js({}) == ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_ipc_command.py -v`
Expected: FAIL — `ImportError: cannot import name 'serialize_command'`（且 desktop_pet 顶层 import 触发 PySide6 等重依赖可能报错）。

> 若 `import desktop_pet` 因 PySide6/webview 等副作用失败：把 `serialize_command`/`apply_command_js` 设计为**纯函数、无第三方依赖**，放在 `desktop_pet.py` 模块顶层，使其可被测试 import 而不触发 Qt 初始化。若仍难隔离，改放到新文件 `core/ipc_command.py` 并在 `desktop_pet.py` 导入。下面 Step 3 采用后者以保可测性。

- [ ] **Step 3: 新建 core/ipc_command.py（纯函数，可测）**

创建 `core/ipc_command.py`：

```python
"""桌宠 overlay→renderer 命令的序列化与 JS 应用（纯函数，无第三方依赖）。

命令走 mp.Pipe(duplex=True) 反向通道，替代旧的 pet_command.json 文件轮询。
"""
from __future__ import annotations
from typing import Any


def serialize_command(**payload: Any) -> tuple[str, dict]:
    """把 emotion/speaking 等关键字段打包成管道消息。"""
    return ("command", payload)


def apply_command_js(payload: dict) -> str:
    """把命令 payload 翻译成 Live2D 页面可执行的 JS 语句（多条用换行拼接）。

    空 payload 返回空串。
    """
    lines: list[str] = []
    if "emotion" in payload:
        lines.append(f"window.__amadeus.setEmotion({payload['emotion']!r})")
    if "speaking" in payload:
        value = "true" if payload["speaking"] else "false"
        lines.append(f"window.__amadeus.setSpeaking({value})")
    return "\n".join(lines)
```

更新 `tests/test_ipc_command.py` 顶部 import 改为：

```python
from core.ipc_command import serialize_command, apply_command_js
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_ipc_command.py -v`
Expected: PASS（8 项全过）。

- [ ] **Step 5: 修改 desktop_pet.py——管道改 duplex=True**

把 `main()` 中的：

```python
    parent_connection, child_connection = mp.Pipe(duplex=False)
```

改为：

```python
    parent_connection, child_connection = mp.Pipe(duplex=True)
```

- [ ] **Step 6: 修改 renderer——用管道收命令，删 COMMAND_FILE 轮询**

在 `desktop_pet.py` 顶部 import 区，把：

```python
from core.ipc_command import apply_command_js
```

加入（紧接现有 `from core.storage import APP_DIR as _APP_DIR` 之后）。

删除顶部 `COMMAND_FILE = _APP_DIR / "pet_command.json"` 这一行（`READY_FILE` 保留）。

把 `renderer_process` 内 `stream_frames` 的命令轮询块：

```python
                last_command_time = 0.0
                while True:
                    try:
                        command = __import__("json").loads(COMMAND_FILE.read_text(encoding="utf-8"))
                        if command.get("timestamp", 0) > last_command_time:
                            last_command_time = command["timestamp"]
                            if "emotion" in command:
                                window.evaluate_js(f"window.__amadeus.setEmotion({command['emotion']!r})")
                            if "speaking" in command:
                                value = "true" if command["speaking"] else "false"
                                window.evaluate_js(f"window.__amadeus.setSpeaking({value})")
                    except (OSError, ValueError):
                        pass
                    data_url = window.evaluate_js(script)
```

替换为：

```python
                while True:
                    # 接收 overlay→renderer 命令（非阻塞）
                    while connection.poll():
                        try:
                            kind, payload = connection.recv()
                        except (EOFError, OSError):
                            break
                        if kind == "command":
                            js = apply_command_js(payload)
                            if js:
                                window.evaluate_js(js)
                    data_url = window.evaluate_js(script)
```

- [ ] **Step 7: 修改 overlay——加 send_command，替换 send_pet_command 调用**

在 `run_overlay` 函数体内、`class AgentSignals` 之前，加闭包级 helper：

```python
    def send_command(**payload) -> None:
        """overlay→renderer 发送命令（emotion/speaking）。"""
        from core.ipc_command import serialize_command
        try:
            connection.send(serialize_command(**payload))
        except (BrokenPipeError, OSError):
            pass
```

删除 `run_overlay` 内的 `from core.pet_controller import send_pet_command` 这一行。

替换三处调用（用 Edit 工具逐处）：
- `self.speech.speaking_changed.connect(lambda value: send_pet_command(speaking=value))`
  → `self.speech.speaking_changed.connect(lambda value: send_command(speaking=value))`
- `send_pet_command(emotion=parsed.emotion)`
  → `send_command(emotion=parsed.emotion)`
- `send_pet_command(emotion="angry")`
  → `send_command(emotion="angry")`

- [ ] **Step 8: 删除 core/pet_controller.py**

用 DeleteFile 工具删除 `d:\Desktop\Ideas\Amadeus2026\amadeus-py\core\pet_controller.py`。

用 Grep 工具搜索 `pet_controller|send_pet_command|COMMAND_FILE|pet_command` 在所有 `.py`：
Expected: 无匹配（除 docs/lessons 历史记录外无代码引用）。

- [ ] **Step 9: 验证导入**

Run: `python -c "import desktop_pet; print('ok')"`
Expected: 输出 `ok`，无 ImportError。

- [ ] **Step 10: 手动验证——情绪与语音同步**

Run（非阻塞）: `python main.py`
- 发一条消息触发回复，观察 Live2D 表情随 `[emotion:xxx]` 切换。
- 观察日语 TTS 朗读时 Live2D 嘴部 speaking 动效。
- 拔掉网络/关掉 LLM 端点触发失败，观察表情切到 angry。
Expected: 三项均正常，且 `data/pet_command.json` 不再生成。

- [ ] **Step 11: 提交**

Run:
```
git add -A
git commit -m "refactor: IPC 文件轮询改双向管道（duplex=True），删除 pet_command.json（P0）"
```

---

## Task 6: 打包规范化

**Files:**
- Modify: `Amadeus.spec`
- Create: `docs/build.md`

- [ ] **Step 1: Amadeus.spec 引入版本号**

在 `Amadeus.spec` 顶部（`from PyInstaller.utils.hooks import collect_submodules` 之后）加：

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))
from core.version import __version__
```

并把 `EXE(... name='Amadeus', ...)` 中的 `name='Amadeus'` 改为：

```python
    name=f'Amadeus-{__version__}',
```

- [ ] **Step 2: 确保 version.py 与 ipc_command.py 打入**

`Amadeus.spec` 的 `Analysis` 已含 `['main.py']` 入口，PyInstaller 会自动追踪 `core.version`、`core.ipc_command` 等纯 Python 模块，无需额外配置。无需改动 `datas`。

- [ ] **Step 3: 撰写打包说明 docs/build.md**

创建 `docs/build.md`：

```markdown
# Amadeus 打包说明

## 环境要求
- Windows 10/11
- Python 3.11+
- 依赖：`python -m pip install -r requirements.txt`
- 打包工具：`python -m pip install pyinstaller`

## 打包命令
在项目根目录执行：

```
python -m PyInstaller Amadeus.spec --noconfirm
```

产物：`dist/Amadeus-<version>/Amadeus-<version>.exe`（onefile，无控制台窗口）。

## 版本号
版本号定义在 `core/version.py` 的 `__version__`，打包产物名自动带版本号。
发布新版只需改 `__version__` 后重新打包。

## 验证打包产物
运行 exe：
- 桌宠窗口直接出现（无登录窗）。
- 设置页"关于"显示版本号与 `__version__` 一致。
```

- [ ] **Step 4: 验证 spec 可解析**

Run: `python -c "import ast; ast.parse(open('Amadeus.spec',encoding='utf-8').read()); from core.version import __version__; print('spec ok, version', __version__)"`
Expected: 输出 `spec ok, version 0.2.0`。

> 注：完整 PyInstaller 打包耗时较长且产物大，本步只验证 spec 语法与版本导入；实际打包验证留到 Task 7 验收时由用户决定是否执行。

- [ ] **Step 5: 提交**

Run:
```
git add Amadeus.spec docs/build.md
git commit -m "build: spec 引入版本号 + 打包说明（P0）"
```

---

## Task 7: P0 整体验收与存档

**Files:** 无（验证 + 提交）

- [ ] **Step 1: 全量单元测试**

Run: `python -m pytest tests/ -v`
Expected: tests/test_version.py（5）+ tests/test_ipc_command.py（8）全过，共 13 项 PASS。

- [ ] **Step 2: 启动验收清单**

Run（非阻塞）: `python main.py`，逐项确认：
- [ ] 无登录窗口，红莉栖桌宠直接显示
- [ ] Live2D 正常渲染、可拖拽、工具栏可用
- [ ] 对话发送→回复正常，表情随 emotion 切换
- [ ] 日语 TTS 朗读时嘴部动效
- [ ] 触发失败时表情切 angry
- [ ] `data/pet_command.json` 不再生成
- [ ] 设置页 6 个 tab 齐全，"关于"显示 0.2.0
- [ ] 托盘菜单"显示窗口/退出"可用

- [ ] **Step 3: 确认无残留死代码引用**

用 Grep 工具搜索：`chat_window|companion_window|codex_bridge|hermes_process|run_direct_agent|_on_chat_logout|find_character_by_login|send_pet_command|pet_controller|COMMAND_FILE|MAHO|MAY_|login_window|core\.auth`
Expected: 仅 docs/ 与 lessons.md 历史提及，无 .py 代码引用。

- [ ] **Step 4: 更新 lessons.md**

在 `lessons.md` 末尾追加 P0 执行小结（5 条教训），格式参照既有章节。

- [ ] **Step 5: 最终提交**

Run:
```
git add -A
git commit -m "docs: P0 验收完成 + lessons 更新"
```

---

## Self-Review（计划自审）

**1. Spec 覆盖：** Spec P0 第 7 节 7 项——删死代码(Task2)✓、移除多角色(Task3)✓、去登录(Task3)✓、文件IPC→进程内信号(Task5)✓、统一设置UI(Task4)✓、打包规范化(Task6)✓、版本提示(Task1+Task4)✓。全部覆盖。

**2. 占位扫描：** 无 TBD/TODO；每步均含可执行命令与期望输出；代码块完整。

**3. 类型/命名一致性：** `serialize_command`/`apply_command_js` 在 Task5 Step3 定义后，Step6/7 与测试一致引用；`__version__` 在 Task1 定义后 Task4/Task6 一致引用；`send_command` 闭包名在 Step7 定义并被三处调用一致。

**4. 风险点：** Task5 IPC 重构是最大风险，已用 TDD 锁定纯函数 `serialize_command`/`apply_command_js`，并保留手动验收步骤（Step10）验证情绪/语音同步不回归——呼应 [lessons.md](../../../lessons.md) 教训2。Task3 重写 main.py 改变启动流程，Step8 手动验证兜底。
