"""renderer 子进程：webview 透明窗口加载 Live2D 并回传帧（从 desktop_pet.py 提出）。

★注意：import webview 必须放在函数内部——主进程若提前加载 Qt，
子进程的 QtWebEngine 渲染必崩（lessons 8-15 事故）。
本模块顶层不得 import PySide6 / webview。
"""
from __future__ import annotations

import base64
import json
import threading
import time
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from multiprocessing.connection import Connection
from pathlib import Path
import socket
import sys


from core.diag import _write_runtime_log
from core.ipc_command import apply_command_js


ROOT = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


# ============================================================
# 类：QuietHandler
# 作用：HTTP 服务处理器——给 renderer 进程提供静态文件（Live2D 页面）。
#       继承 SimpleHTTPRequestHandler，只覆盖了"不打印访问日志"。
# ============================================================
class QuietHandler(SimpleHTTPRequestHandler):
    # ============================================================
    # 函数：__init__()
    # 作用：构造时指定静态文件根目录为项目根（ROOT）
    # 参数：*args/**kwargs 透传给父类
    # 返回值：无（None）
    # ============================================================
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # ============================================================
    # 函数：log_message()
    # 作用：覆盖父类的日志打印为"什么都不做"，避免请求日志刷屏
    # 参数：format/*args 透传（本来要打印的日志内容，这里直接丢弃）
    # 返回值：无（None）
    # ============================================================
    def log_message(self, format, *args):
        pass


# ============================================================
# 函数：free_port()
# 作用：获取一个系统空闲的端口号（绑定 127.0.0.1:0 让系统随机分配，
#       拿到端口号后立即关闭释放）。用于 renderer 进程的本地 HTTP 服务。
# 参数：无
# 返回值：int —— 空闲端口号
# ============================================================
def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ============================================================
# 函数：renderer_process()
# 作用：★renderer 子进程的入口（由桌面宠进程 mp.Process 拉起）。
#       在这个独立进程里：启动本地 HTTP 服务器（提供 Live2D 页面静态文件），
#       用 pywebview 创建透明网页窗口加载 Live2D 模型。
#       主循环（stream_frames）：
#         1. 接收 overlay 主进程发来的命令（如"说话/表情"）→ 用 JS 驱动 Live2D
#         2. 截取网页 canvas 画面 → 通过 mp.Pipe 发回主进程显示
#         3. 每秒 15 帧
#       ★注意：import webview 必须放在函数内部（不能放文件顶部）——
#       主进程若提前加载 Qt，子进程的 QtWebEngine 渲染必崩（lessons 8-15 事故）。
# 参数：
#   connection Connection mp.Pipe 的一端（子进程端），用来和主进程收发数据
# 返回值：无（None，函数结束后子进程退出）
# ============================================================
def renderer_process(connection: Connection) -> None:
    try:
        import webview

        port = free_port()
        server = ThreadingHTTPServer(("127.0.0.1", port), QuietHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = (
            f"http://127.0.0.1:{port}/live2d/phone_live2d_page.html"
            f"?model=/resources/live2d/kurisu/amadeusV1.model3.json"
        )

        window = webview.create_window(
            "Amadeus Renderer", url=url, width=304, height=585,
            x=-10000, y=-10000, frameless=True, shadow=False,
        )

        def loaded() -> None:
            # ============================================================
            # 函数：stream_frames()
            # 作用：★renderer 主循环（独立线程，daemon=True）。
            #       1. 等模型就绪
            #       2. 循环：接收命令→驱动动作；监听悬浮命令；截取"#app 整页"
            #         （透明画布+角色一体）回传主进程 paintEvent 直接显示
            #       每秒 15 帧
            # ============================================================
            def stream_frames() -> None:
                try:
                    for _ in range(30):
                        time.sleep(0.25)
                        if window.evaluate_js("document.title") == "KURISU_READY":
                            break
                    else:
                        connection.send(("error", "Live2D model did not become ready"))
                        return

                    # 调 JS 端的 __amadeusComposite()：只返回透明 Live2D PNG。
                    script = (
                        "(function(){"
                        "  return typeof window.__amadeusComposite === 'function'"
                        "    ? window.__amadeusComposite()"
                        "    : (window.__amadeus && window.__amadeus.app"
                        "       ? window.__amadeus.app.renderer.extract.canvas("
                        "         window.__amadeus.app.stage).toDataURL('image/png')"
                        "       : null);"
                        "})()"
                    )
                    while True:
                        # 接收 overlay→renderer 命令（非阻塞，drain 队列）
                        while connection.poll():
                            try:
                                kind, payload = connection.recv()
                            except (EOFError, OSError):
                                break
                            if kind == "command":
                                js = apply_command_js(payload)
                                if js:
                                    window.evaluate_js(js)
                        # 轮询 JS 端点击事件（Live2D 人物点击）
                        click_json = window.evaluate_js(
                            "(function(){"
                            "  var f = window.__amadeus && window.__amadeus.getClickEvent"
                            "    ? window.__amadeus.getClickEvent()"
                            "    : null;"
                            "  return f ? JSON.stringify(f) : null;"
                            "})()"
                        )
                        if click_json:
                            connection.send(("click", json.loads(click_json)))
                        data_url = window.evaluate_js(script)
                        if not data_url:
                            time.sleep(1 / 15)
                            continue
                        frame = base64.b64decode(data_url.split(",", 1)[1])
                        connection.send(("frame", frame))
                        time.sleep(1 / 15)
                except (BrokenPipeError, EOFError, OSError):
                    pass
                except Exception:
                    path = _write_runtime_log("renderer-crash.log", traceback.format_exc())
                    try:
                        connection.send(("error", f"Renderer crashed. Log: {path}"))
                    except (BrokenPipeError, OSError):
                        pass
                finally:
                    try:
                        window.destroy()
                    except Exception:
                        pass

            threading.Thread(target=stream_frames, daemon=True).start()

        window.events.loaded += loaded
        try:
            webview.start(gui="edgechromium", debug=False)
        finally:
            server.shutdown()
    except Exception:
        path = _write_runtime_log("renderer-crash.log", traceback.format_exc())
        try:
            connection.send(("error", f"Renderer failed to start. Log: {path}"))
        except (BrokenPipeError, OSError):
            pass
