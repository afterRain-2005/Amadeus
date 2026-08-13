"""Amadeus desktop agent entry point."""
from __future__ import annotations

import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon

from ui.login_window import LoginWindow


# 全局窗口引用，防止被 Python gc 回收导致窗口闪退
_login_window: LoginWindow | None = None
_tray_icon: QSystemTrayIcon | None = None
_pet_process: subprocess.Popen | None = None


def _is_frozen() -> bool:
    """PyInstaller 冻结模式检测。"""
    return getattr(sys, 'frozen', False)


def _resource_dir() -> Path:
    """返回资源根目录（冻结时为 sys._MEIPASS，开发时为本文件所在目录）。"""
    if _is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _start_pet_process() -> None:
    """启动桌宠进程。

    冻结模式下用 [exe, '--desktop-pet'] 自调用；
    开发模式下用 [python, 'desktop_pet.py'] 启动子进程。
    """
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
    global _login_window, _tray_icon

    print("[main] 启动 QApplication...", flush=True)
    app = QApplication(sys.argv)
    app.setApplicationName("Amadeus")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(str(_resource_dir() / "resources" / "Kurisu.png")))

    # 注：原 addApplicationFont("Cinzel") 传的是字体族名而非 .ttf 文件路径，
    # 永远返回 -1 无效。Cinzel 字体若已安装到系统，QFont("Cinzel") 可直接使用；
    # 若未安装则 QFont 会自动 fallback，无需此处加载。

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
        _start_pet_process()


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
        _start_pet_process()
    elif _login_window is not None and _login_window.isVisible():
        _login_window.show()
        _login_window.raise_()


if __name__ == "__main__":
    if "--desktop-pet" in sys.argv:
        # 冻结模式下 exe 自调用：入口分流到 desktop_pet.main()
        from desktop_pet import main as pet_main
        sys.exit(pet_main())
    sys.exit(main())
