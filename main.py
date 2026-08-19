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


# ============================================================
# 函数：_is_frozen()
# 作用：判断当前程序是"打包后的 exe"还是"开发模式直接跑 python"。
#       PyInstaller 打包后会给 sys 增加一个 frozen 属性，开发模式没有。
# 参数：无
# 返回值：bool —— True=打包版 exe；False=开发模式
# ============================================================
def _is_frozen() -> bool:
    return getattr(sys, 'frozen', False)


# ============================================================
# 函数：_resource_dir()
# 作用：返回资源文件（图标/图片等）所在的目录。
#       打包版：资源被解压到临时目录 sys._MEIPASS（每次启动目录名随机）；
#       开发版：资源就在项目根目录。
# 参数：无
# 返回值：Path —— 资源目录的绝对路径
# ============================================================
def _resource_dir() -> Path:
    if _is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


# ============================================================
# 函数：_start_pet_process()
# 作用：拉起桌宠子进程（用 subprocess 启动另一个 Python 程序）。
#       ★ 关键点：返回的 Popen 句柄必须存进全局变量 _pet_process，
#       否则退出时无法关闭桌宠，会留下"孤儿进程"（lessons.md 8-17 教训）。
#       打包版：exe 用 --desktop-pet 参数自调用；
#       开发版：直接跑 desktop_pet.py 脚本。
# 参数：无
# 返回值：无（None）
# ============================================================
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


# ============================================================
# 函数：main()
# 作用：程序主入口（托盘进程）。
#       1. 创建 QApplication（Qt 程序的心脏，每个 Qt 程序只能有一个）
#       2. 拉起桌宠子进程
#       3. 创建系统托盘图标 + 右键菜单（显示窗口/退出）
#       4. 调用 app.exec() 进入事件循环 —— 程序在这里无限等待用户操作，
#          直到有人调用 QApplication.quit() 才退出并返回退出码
# 参数：无
# 返回值：int —— Qt 事件循环的退出码（0=正常退出；非0=异常）
# ============================================================
def main() -> int:
    global _tray_icon
    print("[main] Starting QApplication...", flush=True)
    app = QApplication(sys.argv)
    app.setApplicationName("Amadeus")
    app.setQuitOnLastWindowClosed(True)
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
    print("[main] Pet process started, entering event loop...", flush=True)
    return app.exec()


# ============================================================
# 函数：_on_quit()
# 作用：退出处理（用户点托盘菜单"退出"时被调用）。
#       先检查桌宠子进程是否还活着（poll() 返回 None=活着），
#       活着就先 terminate() 杀掉它，再退出自己。
#       顺序很重要：先杀子进程，否则子进程会变成孤儿进程。
# 参数：无
# 返回值：无（None）
# ============================================================
def _on_quit() -> None:
    global _pet_process
    print("[main] Quitting...", flush=True)
    if _pet_process is not None and _pet_process.poll() is None:
        _pet_process.terminate()
    QApplication.quit()


# ============================================================
# 函数：_show_windows()
# 作用：托盘"显示窗口"菜单的响应函数 —— 如果桌宠没在跑，就重新拉起它。
# 参数：无
# 返回值：无（None）
# ============================================================
def _show_windows() -> None:
    global _pet_process
    if _pet_process is None or _pet_process.poll() is not None:
        _start_pet_process()


# ============================================================
# 入口守卫：同一个 exe 用不同参数扮演不同角色
#   1. mp.freeze_support()：打包后 multiprocessing 正常工作需要（开发模式无害）
#   2. mp.parent_process() 不是 None → 我是被 fork 出来的 worker → 立即自杀
#      （lessons.md 8-15 事故：漏拦会无限递归 spawn，进程树每秒 +1）
#   3. 参数带 --desktop-pet → 我是桌宠进程 → 跑 desktop_pet 的 main
#   4. 都不是 → 我是托盘进程 → 跑本文件的 main
# ============================================================
if __name__ == "__main__":
    # 冻结模式下 multiprocessing spawn 会重新执行本 exe（--multiprocessing-fork）。
    # 实测 PyInstaller 6.21 bootloader 会把 worker argv 里的 --multiprocessing-fork
    # 剥离（Python 层 sys.argv 只剩业务参数），freeze_support/is_forking 永远拦不住；
    # worker 恢复协议一旦失败即落入下方 argv 分发 → 误入 pet_main → 无限递归 spawn
    # （实测进程树每秒 +1）。
    # mp.parent_process() 由 spawn 协议设置在进程对象上、不依赖 argv，是 frozen 下
    # 判定「本进程是 mp worker」的唯一可靠手段：worker 绝不进入任何业务入口。
    import multiprocessing as mp
    mp.freeze_support()
    if mp.parent_process() is not None:
        # 漏拦的 worker：立即非零退出，让父进程通过 join/exitcode 感知 renderer
        # 启动失败，而不是递归复制出整棵桌宠进程树。
        sys.exit(1)
    if "--desktop-pet" in sys.argv:
        from desktop_pet import main as pet_main
        sys.exit(pet_main())
    sys.exit(main())
