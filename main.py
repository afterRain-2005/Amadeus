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
