"""Bounded Windows desktop tools exposed to the companion agent."""
from __future__ import annotations

import base64
from io import BytesIO
import os
from pathlib import Path
import shlex
import subprocess
import webbrowser
import re

from PIL import ImageGrab
import win32clipboard
import win32con
import win32gui
import httpx

from config import OPENCLAW_DEFAULTS


POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
DEFAULT_WORKDIR = str(Path(__file__).resolve().parents[4])


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
    import trafilatura  # 延迟导入：避免启动时强依赖，仅 fetch_url 调用时才需要
    return trafilatura.extract(html) or ""


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


TOOL_DEFINITIONS = [
    {"type": "function", "function": {"name": "capture_screen", "description": "Capture the current desktop. Use only when the configured model supports image input; otherwise use list_windows and terminal inspection.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "list_windows", "description": "List visible desktop windows and titles.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "read_clipboard", "description": "Read text from the Windows clipboard.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "open_target", "description": "Open an application, file, folder, or URL.", "parameters": {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}}},
    {"type": "function", "function": {"name": "focus_window", "description": "Bring a visible window to the foreground by title substring.", "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}}},
    {"type": "function", "function": {"name": "type_text", "description": "Type text into the currently focused application.", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "press_keys", "description": "Press a keyboard shortcut such as ctrl+l or alt+tab.", "parameters": {"type": "object", "properties": {"keys": {"type": "array", "items": {"type": "string"}}}, "required": ["keys"]}}},
    {"type": "function", "function": {"name": "click", "description": "Click a desktop coordinate after inspecting the screen.", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "button": {"type": "string", "enum": ["left", "right"]}}, "required": ["x", "y"]}}},
    {"type": "function", "function": {"name": "run_command", "description": "Run PowerShell with a reliable UTF-8 console and return exit code, stdout, and stderr. Use for terminal tasks.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string", "description": "Existing working directory; defaults to the Windows desktop."}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "web_search", "description": "Search the web with DuckDuckGo and return the top 5 results (title, snippet, url). Use for factual questions, current info, weather, etc.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "fetch_url", "description": "Fetch a web page and extract its main text content (up to 8000 chars). Use to read an article or page found via web_search. Only http/https URLs.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "file_find", "description": "Find files matching a glob pattern (e.g. *.txt) under a root directory (defaults to Desktop). Returns up to 30 paths.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "root": {"type": "string", "description": "Directory to search under; defaults to user Desktop."}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "list_dir", "description": "List entries (name, size, type) in a directory. Returns up to 100 entries.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (up to 20000 chars, max 2MB). Rejects binary and paths outside allowed roots.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write text content to a file (overwrites). Path must be inside allowed roots. Requires user confirmation.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "operate_gui", "description": "Delegate a GUI operation task (e.g. 'open Notepad and type hello', 'click the send button in WeChat') to the OpenClaw CUA backend, which drives mouse/keyboard to operate the real desktop. Requires OpenClaw Gateway running locally and a CUA skill installed. Needs user confirmation.", "parameters": {"type": "object", "properties": {"task": {"type": "string", "description": "Natural language description of the GUI operation to perform."}}, "required": ["task"]}}},
]

CONFIRMATION_REQUIRED = {"open_target", "type_text", "press_keys", "click", "run_command", "write_file", "operate_gui"}

_POWERSHELL_CONTROL_TOKENS = (";", "|", "&", "`", "$(", ">", "<", "\n", "\r")


def is_auto_approved_command(command: str, safe_commands: list[str] | tuple[str, ...]) -> bool:
    """仅允许单条安全命令自动放行，禁止 PowerShell 复合/重定向语法。"""
    text = str(command or "").strip()
    if not text or any(token in text for token in _POWERSHELL_CONTROL_TOKENS):
        return False
    try:
        parts = shlex.split(text, posix=False)
    except ValueError:
        return False
    if not parts:
        return False
    command_name = parts[0].strip("\"'")
    safe = {str(item).casefold() for item in safe_commands}
    return command_name.casefold() in safe


def execute_tool(name: str, arguments: dict) -> dict:
    if name == "capture_screen":
        image = ImageGrab.grab(all_screens=True).convert("RGB")
        image.thumbnail((1440, 900))
        output = BytesIO()
        image.save(output, "JPEG", quality=78)
        return {"text": f"Captured desktop at {image.width}x{image.height}.", "image_url": "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")}
    if name == "list_windows":
        windows = []
        def collect(handle, _):
            title = win32gui.GetWindowText(handle).strip()
            if title and win32gui.IsWindowVisible(handle):
                windows.append(title)
        win32gui.EnumWindows(collect, None)
        return {"text": "\n".join(windows[:80]) or "No visible windows."}
    if name == "read_clipboard":
        win32clipboard.OpenClipboard()
        try:
            text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT) else ""
        finally:
            win32clipboard.CloseClipboard()
        return {"text": text[:12000] or "Clipboard has no text."}
    if name == "open_target":
        target = os.path.expandvars(os.path.expanduser(arguments["target"]))
        if target.startswith(("http://", "https://")):
            webbrowser.open(target)
        else:
            os.startfile(target)
        return {"text": f"Opened {target}"}
    if name == "focus_window":
        needle = arguments["title"].lower()
        found = []
        def focus(handle, _):
            if needle in win32gui.GetWindowText(handle).lower() and win32gui.IsWindowVisible(handle):
                found.append(handle)
        win32gui.EnumWindows(focus, None)
        if not found:
            return {"text": "Window not found."}
        win32gui.ShowWindow(found[0], win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(found[0])
        return {"text": f"Focused {win32gui.GetWindowText(found[0])}"}
    if name == "type_text":
        import time
        win32clipboard.OpenClipboard()
        try:
            previous = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT) else None
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, arguments["text"])
        finally:
            win32clipboard.CloseClipboard()
        _press_keys(["ctrl", "v"])
        time.sleep(0.15)
        if previous is not None:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, previous)
            finally:
                win32clipboard.CloseClipboard()
        return {"text": "Text typed."}
    if name == "press_keys":
        _press_keys(arguments["keys"])
        return {"text": f"Pressed {'+'.join(arguments['keys'])}."}
    if name == "click":
        import win32api
        x, y = int(arguments["x"]), int(arguments["y"])
        win32api.SetCursorPos((x, y))
        down, up = ((win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP) if arguments.get("button") == "right" else (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP))
        win32api.mouse_event(down, x, y, 0, 0)
        win32api.mouse_event(up, x, y, 0, 0)
        return {"text": f"Clicked {x},{y}."}
    if name == "web_search":
        query = arguments["query"].strip()
        if not query:
            return {"text": "Empty query."}
        try:
            from ddgs import DDGS  # 延迟导入：避免启动时强依赖，仅 web_search 调用时才需要
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
    if name == "operate_gui":
        return _operate_gui(arguments)
    if name == "run_command":
        return _run_powershell(arguments)
    raise ValueError(f"Unknown tool: {name}")


def _operate_gui(arguments: dict) -> dict:
    """把 GUI 操作任务委托给本地 OpenClaw Gateway（CUA 后端）。

    通过 POST /v1/chat/completions 把自然语言任务发给 openclaw/default 代理，
    代理自动启用 CUA skill 操作真实桌面（鼠标/键盘）。Gateway 未启用或不可达时返回降级提示。
    参考接口：https://docs.openclaw.ai/gateway（/v1/chat/completions 在主端口，OpenAI 兼容）。
    """
    task = arguments.get("task", "").strip()
    if not task:
        return {"text": "Empty GUI task."}
    if not OPENCLAW_DEFAULTS.get("enabled"):
        return {"text": "OpenClaw CUA 后端未启用。请在 config.py 设置 OPENCLAW_DEFAULTS['enabled']=True，并部署 OpenClaw Gateway（openclaw gateway，默认 127.0.0.1:18789）。"}
    base = str(OPENCLAW_DEFAULTS.get("base_url", "http://127.0.0.1:18789")).rstrip("/")
    token = str(OPENCLAW_DEFAULTS.get("token", ""))
    model = str(OPENCLAW_DEFAULTS.get("model", "openclaw/default"))
    timeout = float(OPENCLAW_DEFAULTS.get("timeout", 120))
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base}/v1/chat/completions",
                headers=headers,
                json={"model": model, "messages": [{"role": "user", "content": task}]},
            )
        if resp.is_error:
            return {"text": f"OpenClaw Gateway HTTP {resp.status_code}: {resp.text[:500]}"}
        data = resp.json()
        choices = data.get("choices") or []
        content = choices[0].get("message", {}).get("content", "") if choices else ""
        return {"text": content or "OpenClaw 返回空回复。"}
    except httpx.HTTPError as exc:
        return {"text": f"OpenClaw Gateway 不可达：{exc}。请确认 Gateway 已启动（openclaw gateway）。"}
    except Exception as exc:
        return {"text": f"operate_gui 失败：{exc}"}


def _press_keys(keys: list[str]) -> None:
    import win32api
    mapping = {"ctrl": win32con.VK_CONTROL, "alt": win32con.VK_MENU, "shift": win32con.VK_SHIFT,
               "enter": win32con.VK_RETURN, "tab": win32con.VK_TAB, "esc": win32con.VK_ESCAPE,
               "win": win32con.VK_LWIN, "space": win32con.VK_SPACE}
    codes = [mapping.get(key.lower(), ord(key.upper())) for key in keys]
    for code in codes:
        win32api.keybd_event(code, 0, 0, 0)
    for code in reversed(codes):
        win32api.keybd_event(code, 0, win32con.KEYEVENTF_KEYUP, 0)


def _run_powershell(arguments: dict) -> dict:
    command = str(arguments.get("command", "")).strip()
    if not command:
        raise ValueError("PowerShell command is empty.")
    cwd = Path(os.path.expandvars(os.path.expanduser(arguments.get("cwd") or DEFAULT_WORKDIR))).resolve()
    if not cwd.is_dir():
        raise ValueError(f"Working directory does not exist: {cwd}")
    timeout = max(1, min(int(arguments.get("timeout_seconds", 45)), 120))
    script = (
        "$ErrorActionPreference='Continue';"
        "$ProgressPreference='SilentlyContinue';"
        "[Console]::InputEncoding=[System.Text.UTF8Encoding]::new($false);"
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
        "$OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
        + command
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    completed = subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        cwd=str(cwd), capture_output=True, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace").strip()
    stderr = _clean_powershell_stderr(completed.stderr.decode("utf-8", errors="replace").strip())
    return {
        "text": (
            f"exit_code: {completed.returncode}\n"
            f"cwd: {cwd}\n"
            f"stdout:\n{stdout[:12000] or '<empty>'}\n"
            f"stderr:\n{stderr[:4000] or '<empty>'}"
        )
    }


def _clean_powershell_stderr(text: str) -> str:
    if not text.startswith("#< CLIXML"):
        return text
    decoded = text.replace("_x000D__x000A_", "\n")
    errors = re.findall(r'<S S="Error">(.*?)</S>', decoded, flags=re.DOTALL)
    if not errors:
        return ""
    import html
    return "\n".join(html.unescape(error) for error in errors).strip()
