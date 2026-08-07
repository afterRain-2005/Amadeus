"""Bounded Windows desktop tools exposed to the companion agent."""
from __future__ import annotations

import base64
from io import BytesIO
import os
from pathlib import Path
import subprocess
import webbrowser
import re

from PIL import ImageGrab
import win32clipboard
import win32con
import win32gui


POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
DEFAULT_WORKDIR = str(Path(__file__).resolve().parents[4])


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
]

CONFIRMATION_REQUIRED = {"open_target", "type_text", "press_keys", "click", "run_command"}


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
    if name == "run_command":
        return _run_powershell(arguments)
    raise ValueError(f"Unknown tool: {name}")


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
