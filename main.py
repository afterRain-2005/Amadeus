"""Amadeus desktop agent entry point."""
from __future__ import annotations

import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QFontDatabase, QIcon

from ui.login_window import LoginWindow


# 全局窗口引用，防止被 Python gc 回收导致窗口闪退
_login_window: LoginWindow | None = None
_tray_icon: QSystemTrayIcon | None = None
_pet_process: subprocess.Popen | None = None


def main() -> int:
    global _login_window, _tray_icon

    print("[main] 启动 QApplication...", flush=True)
    app = QApplication(sys.argv)
    app.setApplicationName("Amadeus")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "resources" / "Kurisu.png")))

    QFontDatabase.addApplicationFont("Cinzel")

    print("[main] 创建登录窗口...", flush=True)
    _login_window = LoginWindow()
    _login_window.login_success.connect(_on_login_success)
    _login_window.show()
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
    print("[main] 登录窗口已 show()，进入事件循环...", flush=True)

    return app.exec()


def _on_login_success(character_id: str) -> None:
    """Hide login and start the integrated Live2D desktop agent."""
    global _pet_process
    print(f"[main] 登录成功 character_id={character_id}", flush=True)
    _login_window.hide()

    if _pet_process is None or _pet_process.poll() is not None:
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        _pet_process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve().parent / "desktop_pet.py")],
            cwd=Path(__file__).resolve().parent,
            creationflags=creation_flags,
        )



def _on_chat_logout() -> None:
    """Stop the pet and return to login."""
    global _pet_process
    print("[main] 退出登录，切回登录窗口", flush=True)
    if _pet_process is not None and _pet_process.poll() is None:
        _pet_process.terminate()
        _pet_process = None
    if _login_window is not None:
        _login_window.show()
        _login_window.raise_()
        _login_window.activateWindow()


def _on_quit() -> None:
    """退出整个程序。"""
    global _pet_process
    print("[main] 退出程序", flush=True)
    if _pet_process is not None and _pet_process.poll() is None:
        _pet_process.terminate()
    QApplication.quit()


def _show_windows() -> None:
    global _pet_process
    if _pet_process is None or _pet_process.poll() is not None:
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        _pet_process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve().parent / "desktop_pet.py")],
            cwd=Path(__file__).resolve().parent, creationflags=creation_flags,
        )
    elif _login_window is not None and _login_window.isVisible():
        _login_window.show()
        _login_window.raise_()


if __name__ == "__main__":
    sys.exit(main())
