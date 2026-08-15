"""Pure-Python Live2D desktop pet with a native transparent overlay."""
from __future__ import annotations

import base64
import html
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import multiprocessing as mp
from multiprocessing.connection import Connection
from pathlib import Path
import socket
import os
import subprocess
import sys
import threading
import time


ROOT = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
# 本地依赖目录：用 pip install --target 装的 ddgs/trafilatura 等（避开 anaconda site-packages 沙箱限制）。
# 冻结模式下依赖已打包进 exe，不需要 .libs。
if not getattr(sys, 'frozen', False):
    _libs = ROOT / ".libs"
    if _libs.is_dir():
        sys.path.insert(0, str(_libs))
# 通信走 mp.Pipe(duplex=True) 双向管道（frame 下行 / command 上行），
# 不再用 data/pet_command.json 文件轮询。
from core.ipc_command import apply_command_js
from core.storage import APP_DIR as _APP_DIR
READY_FILE = _APP_DIR / "desktop_pet.ready"


class QuietHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, format, *args):
        pass


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _locate_gpt_sovits(root: Path) -> tuple[Path, Path] | None:
    """定位 (venv_python, GPT-SoVITS 目录)。

    dev：ROOT/gpt_sovits_venv + ROOT/GPT-SoVITS。
    frozen：ROOT 是 _MEIPASS 临时目录不可用，改探 exe 同级及其父级
    （exe 常放 dist\，父级即项目根）。
    """
    exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else root
    for base in (root, root.parent, exe_dir, exe_dir.parent):
        venv_python = base / "gpt_sovits_venv" / "Scripts" / "python.exe"
        api_dir = base / "GPT-SoVITS"
        if venv_python.exists() and (api_dir / "api_v2.py").exists():
            return venv_python, api_dir
    return None


def maybe_start_gpt_sovits(spawn=subprocess.Popen) -> bool:
    """GPT-SoVITS API 不在线时后台拉起（幂等：在线则跳过）。

    返回是否发起了启动进程。拉起后模型加载需数十秒，由 SpeechPlayer
    的 available TTL 重查（60s）自愈衔接，不阻塞 UI。
    """
    try:
        from core.gpt_sovits_client import KurisuTTS
        if KurisuTTS().available:
            return False
    except Exception:
        pass
    located = _locate_gpt_sovits(ROOT)
    if not located:
        return False
    venv_python, api_dir = located
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    # stdout/stderr 必须重定向：CREATE_NO_WINDOW 且无重定向时 std 句柄为 NULL，
    # sys.stdout/stderr 为 None，GPT-SoVITS 加载途中会静默死亡（实测）。
    # 重定向到日志同时保留 GPU/模型加载诊断信息。
    log_file = None
    try:
        log_file = open(api_dir / "api_autostart.log", "w", encoding="utf-8", errors="replace")
        stdout = log_file
    except OSError:
        stdout = subprocess.DEVNULL
    try:
        spawn(
            [str(venv_python), "api_v2.py"],
            cwd=str(api_dir),
            stdout=stdout,
            stderr=subprocess.STDOUT,
            creationflags=creation,
        )
        return True
    except OSError:
        if log_file is not None:
            log_file.close()
        return False


def renderer_process(connection: Connection) -> None:
    import webview

    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), QuietHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/live2d/live2d_page.html?model=/resources/live2d/kurisu/amadeusV1.model3.json"
    window = webview.create_window(
        "Amadeus Renderer", url=url, width=420, height=680,
        x=-10000, y=-10000, frameless=True, shadow=False,
    )

    def loaded() -> None:
        def stream_frames() -> None:
            try:
                for _ in range(30):
                    time.sleep(0.25)
                    if window.evaluate_js("document.title") == "KURISU_READY":
                        break
                else:
                    connection.send(("error", "Live2D model did not become ready"))
                    return

                script = "window.__amadeus.app.renderer.extract.canvas(window.__amadeus.app.stage).toDataURL('image/png')"
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
                    data_url = window.evaluate_js(script)
                    frame = base64.b64decode(data_url.split(",", 1)[1])
                    connection.send(("frame", frame))
                    time.sleep(1 / 15)
            except (BrokenPipeError, EOFError, OSError):
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


def _decide_delta_action(
    streamed_reply: str, text: str, history_expanded: bool
) -> tuple[str, bool, bool]:
    """流式增量到达时的纯决策逻辑。

    返回 (new_streamed, should_show_thinking, should_set_bubble_text)：
    - 始终把 text 累积到 streamed_reply；
    - delta 期间不更新气泡文字（should_set_bubble_text 恒为 False），由 finished 阶段
      统一 _show_layered_bubbles；
    - 仅在历史面板未展开时显示思考点动画（避免与历史面板重复展示）。
    """
    new_streamed = streamed_reply + text
    should_show_thinking = not history_expanded
    should_set_bubble_text = False
    return new_streamed, should_show_thinking, should_set_bubble_text


def _decide_send_instant_action() -> dict:
    """_send 发送瞬间的即时反应决策：呼吸动画 + thinking emotion。
    返回 {show_thinking_dots, emotion}。不再用静态'让我想想…'。"""
    return {"show_thinking_dots": True, "emotion": "thinking"}


def _decide_call_toggle_action(in_call: bool) -> dict:
    """Dock 电话按钮点击决策：非通话态→进入通话，通话态→挂断。

    返回 {enter_call, hangup}。纯函数便于测试（参考 _decide_delta_action 模式）。
    """
    if in_call:
        return {"enter_call": False, "hangup": True}
    return {"enter_call": True, "hangup": False}


def _build_kurisu_html(text: str) -> str:
    """Kurisu 消息 HTML：fauux 玫瑰软底 + 玫瑰左边条。"""
    safe = html.escape(text).replace("\n", "<br>")
    return (
        "<div style='margin:0 0 12px 0;padding:8px 10px;background:rgba(210,115,138,0.22);"
        "border-left:2px solid #d2738a;border-radius:4px'>"
        "<div style='color:#d2738a;font-weight:bold;font-size:11px;margin-bottom:2px'>Kurisu</div>"
        f"<div style='line-height:1.42;color:#c1b492;font-size:13px'>{safe}</div></div>"
    )


def _build_you_html(text: str) -> str:
    """You 消息 HTML：淡玫瑰底 + 右米黄边条。"""
    safe = html.escape(text).replace("\n", "<br>")
    return (
        "<div style='margin:0 0 12px 0;padding:8px 10px;background:rgba(210,115,138,0.14);"
        "border-right:2px solid #8a7f63;border-radius:4px;text-align:right'>"
        "<div style='color:#8a7f63;font-weight:bold;font-size:11px;margin-bottom:2px'>You</div>"
        f"<div style='line-height:1.42;color:#c1b492;font-size:13px'>{safe}</div></div>"
    )


def _dither_texture_url() -> str:
    """fauux 抖动纹理的绝对路径（正斜杠，供 QSS url() 引用）。"""
    return str(ROOT / "resources" / "textures" / "dither_rose.png").replace("\\", "/")


def _ensure_dither_texture() -> None:
    """首次运行时生成 16×16 抖动纹理（已存在则跳过）。失败退化为纯色。"""
    target = ROOT / "resources" / "textures" / "dither_rose.png"
    if target.exists():
        return
    try:
        from PySide6.QtGui import QColor, QImage, QPainter
        target.parent.mkdir(parents=True, exist_ok=True)
        img = QImage(16, 16, QImage.Format_ARGB32)
        img.fill(QColor("#171114"))
        p = QPainter(img)
        try:
            rose = QColor(210, 115, 138, 56)
            cream = QColor(193, 180, 146, 26)
            dark = QColor(0, 0, 0, 128)
            for y in range(0, 16, 4):
                for x in range(0, 16, 4):
                    p.setPen(rose);  p.drawPoint(x, y)
                    p.setPen(cream); p.drawPoint(x + 2, y + 2)
                    p.setPen(dark);  p.drawPoint(x + 1, y + 3)
                p.setPen(QColor(0, 0, 0, 40))
                p.drawLine(0, y + 3, 15, y + 3)
        finally:
            p.end()
        img.save(str(target), "PNG")
    except OSError:
        pass


def run_overlay(connection: Connection, renderer: mp.Process) -> int:
    from PySide6.QtCore import QByteArray, QEasingCurve, QObject, QPoint, QPropertyAnimation, QRect, QRectF, QRunnable, Qt, QThreadPool, QTimer, Signal
    from PySide6.QtGui import QColor, QIcon, QImage, QKeyEvent, QLinearGradient, QMouseEvent, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import (
                QApplication, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                QPushButton, QSystemTrayIcon, QTextBrowser, QVBoxLayout, QWidget,
                QGraphicsOpacityEffect,
            )

    from config import get_character_by_id, get_random_greeting
    from core.agent_client import _load_soul_md
    from core.emotion_parser import parse_reply
    from core.ipc_command import serialize_command
    from core.session_manager import active_session, add_message, load_state, save_state
    from core.storage import load_config
    from core.tts_client import SpeechPlayer
    from ui.settings_dialog import SettingsDialog

    character = get_character_by_id("kurisu")

    def send_command(**payload) -> None:
        """overlay→renderer 发送命令（emotion/speaking），走 duplex 管道。"""
        try:
            connection.send(serialize_command(**payload))
        except (BrokenPipeError, OSError):
            pass

    class AgentSignals(QObject):
        status = Signal(str)
        delta = Signal(str)
        finished = Signal(str)
        failed = Signal(str)
        confirmation = Signal(object)

    class AgentTask(QRunnable):
        def __init__(self, history, memories=None) -> None:
            super().__init__()
            self.history = history
            self.memories = memories or []
            self.signals = AgentSignals()

        def run(self) -> None:
            config = load_config()
            # 读取 SOUL.md（若存在），否则回退到 config.py 中的 KURISU_PERSONALITY
            soul_md = _load_soul_md("kurisu") or character.personality
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

        def _handle_approval(self, payload: dict) -> str:
            import threading
            request = {"payload": payload, "event": threading.Event(), "choice": "deny"}
            self.signals.confirmation.emit(request)
            request["event"].wait()
            return request["choice"]

    class DockButton(QPushButton):
        """Dock 单个按钮：SVG 图标 + hover 放大。"""
        BASE_SIZE = 32
        HOVER_SIZE = 44
        NEAR_SIZE = 38

        def __init__(self, icon_name: str, tooltip: str, is_danger: bool = False, parent=None):
            super().__init__(parent)
            self._icon_name = icon_name
            self._is_danger = is_danger
            self.setFixedSize(self.BASE_SIZE, self.BASE_SIZE)
            self.setToolTip(tooltip)
            self.setCursor(Qt.PointingHandCursor)
            self._renderer = QSvgRenderer(QByteArray(
                (ROOT / "resources" / "icons" / f"{icon_name}.svg").read_bytes()
            ))
            self._scale = 1.0
            self._hover_anim = QPropertyAnimation(self, b"scale", self)
            self._hover_anim.setDuration(200)
            self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)

        def get_scale(self) -> float:
            return self._scale

        def set_scale(self, value: float) -> None:
            self._scale = value
            size = int(self.BASE_SIZE * value)
            self.setFixedSize(size, size)
            self.update()

        scale = property(get_scale, set_scale)

        def set_target_scale(self, scale: float) -> None:
            self._hover_anim.stop()
            self._hover_anim.setStartValue(self._scale)
            self._hover_anim.setEndValue(scale)
            self._hover_anim.start()

        def paintEvent(self, event) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            # 背景（fauux：玫瑰 #d2738a / 危险深玫瑰 #7a3040，直角）
            if self._is_danger:
                bg = QColor(122, 48, 64, 40) if self._scale > 1.0 else QColor(122, 48, 64, 26)
                border = QColor(210, 115, 138, 110)
            else:
                bg = QColor(210, 115, 138, 22) if self._scale > 1.0 else QColor(210, 115, 138, 14)
                border = QColor(210, 115, 138, 100)
            painter.setBrush(bg)
            painter.setPen(border)
            painter.drawRect(self.rect())
            # SVG 图标（文件已带颜色，直接渲染）
            pad = 4
            self._renderer.render(painter, QRectF(pad, pad, self.width() - pad * 2, self.height() - pad * 2))

    class DockBar(QWidget):
        """底部悬浮 Dock 工具栏：5 按钮 + hover 邻近放大。"""
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self._buttons: list = []
            layout = QHBoxLayout(self)
            layout.setContentsMargins(8, 4, 8, 4)
            layout.setSpacing(8)
            layout.setAlignment(Qt.AlignCenter)
            self.setLayout(layout)
            self._build_buttons()

        def _build_buttons(self) -> None:
            specs = [
                ("chat", "对话", False),
                ("phone", "电话", False),
                ("pin", "固定", False),
                ("settings", "设置", False),
                ("history", "记录", False),
                ("close", "退出", True),
            ]
            for icon_name, tooltip, is_danger in specs:
                btn = DockButton(icon_name, tooltip, is_danger, self)
                btn.installEventFilter(self)
                self._buttons.append(btn)
                self.layout().addWidget(btn)

        def eventFilter(self, obj, event) -> bool:
            if obj in self._buttons:
                idx = self._buttons.index(obj)
                if event.type() == event.Type.Enter:
                    self._apply_hover_scale(idx)
                elif event.type() == event.Type.Leave:
                    self._apply_leave_scale()
            return super().eventFilter(obj, event)

        def _apply_hover_scale(self, hover_idx: int) -> None:
            for i, btn in enumerate(self._buttons):
                dist = abs(i - hover_idx)
                if dist == 0:
                    btn.set_target_scale(DockButton.HOVER_SIZE / DockButton.BASE_SIZE)
                elif dist == 1:
                    btn.set_target_scale(DockButton.NEAR_SIZE / DockButton.BASE_SIZE)
                else:
                    btn.set_target_scale(1.0)

        def _apply_leave_scale(self) -> None:
            for btn in self._buttons:
                btn.set_target_scale(1.0)

        def button(self, name: str):
            for btn in self._buttons:
                if btn.toolTip() == name:
                    return btn
            raise KeyError(name)

    class HistoryDrawer(QWidget):
        """右侧滑入历史抽屉。"""
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self._width = 168
            layout = QVBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
            self.history = QTextBrowser(self)
            self.history.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
            self.history.setStyleSheet(
                "QTextBrowser{background-color:#171114;background-image:url(" + _dither_texture_url() + ");"
                "color:#c1b492;border:1px solid #d2738a;"
                "border-radius:0px;padding:8px;font:13px 'Times New Roman','SimSun'}"
                "QScrollBar:vertical{background:rgba(210,115,138,0.15);width:6px;margin:4px}"
                "QScrollBar::handle:vertical{background:#d2738a;border-radius:0px;min-height:30px}"
                "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}"
                "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent}"
            )
            self.history.setOpenExternalLinks(False)
            layout.addWidget(self.history)
            self._slide_anim = None

        def set_messages_html(self, html_content: str) -> None:
            self.history.setHtml(
                "<html><body style='margin:0;background:transparent'>"
                + html_content
                + "</body></html>"
            )
            self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())

        def slide_in(self) -> None:
            """300ms 从右滑入。"""
            if self.parent() is None:
                return
            parent_w = self.parent().width()
            target_x = parent_w - self._width - 4
            anim = QPropertyAnimation(self, b"pos", self)
            anim.setDuration(300)
            anim.setStartValue(self.pos())
            anim.setEndValue(QPoint(target_x, self.pos().y()))
            anim.setEasingCurve(QEasingCurve.InOutCubic)
            anim.start()
            self._slide_anim = anim

        def slide_out(self) -> None:
            """300ms 滑出到右侧外。"""
            if self.parent() is None:
                return
            parent_w = self.parent().width()
            target_x = parent_w + 4
            anim = QPropertyAnimation(self, b"pos", self)
            anim.setDuration(300)
            anim.setStartValue(self.pos())
            anim.setEndValue(QPoint(target_x, self.pos().y()))
            anim.setEasingCurve(QEasingCurve.InOutCubic)
            anim.start()
            self._slide_anim = anim

    class PetWindow(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self._frame = QImage()
            self._first_frame_received = False
            self._drag_offset: QPoint | None = None
            self._busy = False
            self._streamed_reply = ""
            self._hotkey_down = False
            self._csa_down = False
            self._history_expanded = False
            self._zoom = 0.9
            self._pinned = False
            self._bubble_segments: list[str] = []
            self._bubble_index = 0
            self._bubble_timer: QTimer | None = None
            self._snap_anim: QPropertyAnimation | None = None
            self._user_pos: QPoint | None = None
            self._was_desktop = False
            self._inactivity_timer = QTimer(self)
            self._inactivity_timer.setSingleShot(True)
            self._inactivity_timer.timeout.connect(self._hide_idle_bubble)
            self._state = load_state(character.id, get_random_greeting(character.id))
            self.speech = SpeechPlayer(self)
            self.speech.speaking_changed.connect(lambda value: send_command(speaking=value))
            # 语音服务离线：气泡序列末尾追加提示（信号在 TTS 工作线程发射，
            # queued connection 回到主线程，追加列表安全）
            self.speech.tts_offline.connect(self._notify_tts_offline)
            self.setWindowTitle("牧濑红莉栖 [PY]")
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_NoSystemBackground, True)
            self.setFixedSize(400, 680)

            screen = QApplication.primaryScreen().availableGeometry()
            # 底部留 60px 余量，避免输入框（距窗口底 8px）被任务栏/屏幕底遮挡
            self.move(screen.right() - self.width() - 20, screen.bottom() - self.height() - 60)

            self.reply_bubble = QLabel(self)
            self.reply_bubble.setGeometry((self.width() - 390) // 2, 8, 390, 96)
            self.reply_bubble.setAlignment(Qt.AlignCenter)
            self.reply_bubble.setWordWrap(True)
            self.reply_bubble.setStyleSheet(
                "QLabel{background-color:#171114;background-image:url(" + _dither_texture_url() + ");"
                "color:#c1b492;"
                "border:1px solid #d2738a;border-radius:0px;"
                "padding:10px 16px;font:14px 'Times New Roman','SimSun';"
                "font-weight:400}"
            )
            self._set_bubble_text(self._latest_line(active_session(self._state)["messages"][-1]["content"]))
            self.reply_bubble.hide()

            # 输入面板（默认隐藏，点击💬展开）
            panel_w = 365
            panel_x = (self.width() - panel_w) // 2
            self.input_panel = QWidget(self)
            self.input_panel.setGeometry(panel_x, self.height() - 70, panel_w, 52)
            self.input_panel.setStyleSheet(
                "background-color:#171114;background-image:url(" + _dither_texture_url() + ");"
                "border:1px solid #d2738a;border-radius:0px"
            )
            input_layout = QHBoxLayout(self.input_panel)
            input_layout.setContentsMargins(14, 6, 6, 6)
            input_layout.setSpacing(4)
            self.input = QLineEdit()
            self.input.setPlaceholderText("和红莉栖对话…")
            self.input.setStyleSheet(
                "QLineEdit{background:transparent;color:#c1b492;border:0;padding:8px 10px;"
                "font-size:14px;font-family:'Times New Roman','SimSun'}"
                "QLineEdit::placeholder{color:#8a7f63}"
            )
            self.input.returnPressed.connect(self._send)
            collapse_button = QPushButton("←")
            collapse_button.setToolTip("收起聊天")
            collapse_button.clicked.connect(self._toggle_input_panel)
            collapse_button.setFixedSize(36, 36)
            collapse_button.setStyleSheet(
                "QPushButton{background:transparent;color:#8a7f63;border:1px solid #8a7f63;border-radius:0px;"
                "font-size:16px}"
                "QPushButton:hover{background:#d2738a;color:#171114}"
            )
            input_layout.addWidget(collapse_button)
            input_layout.addWidget(self.input, 1)
            send_button = QPushButton("↑")
            send_button.setToolTip("发送")
            send_button.clicked.connect(self._send)
            send_button.setFixedSize(36, 36)
            send_button.setStyleSheet(
                "QPushButton{background:transparent;color:#c1b492;border:1px solid #c1b492;border-radius:0px;"
                "font-size:16px;font-weight:bold}"
                "QPushButton:hover{background:#d2738a;color:#171114} QPushButton:disabled{color:#8a7f63}"
            )
            input_layout.addWidget(send_button)
            self.send_button = send_button
            self.input_panel.hide()

            # Dock 底部悬浮工具栏
            self.dock_bar = DockBar(self)
            self.dock_bar.button("对话").clicked.connect(self._toggle_input_panel)
            self.dock_bar.button("固定").clicked.connect(self._toggle_pin)
            self.dock_bar.button("设置").clicked.connect(lambda: SettingsDialog(self).exec())
            self.dock_bar.button("记录").clicked.connect(self._toggle_history)
            self.dock_bar.button("退出").clicked.connect(QApplication.quit)
            self.dock_bar.button("电话").clicked.connect(self._toggle_call)
            self.dock_bar.show()

            # 通话态视图（默认隐藏）
            from ui.widgets.call_view import CallView
            self._in_call = False
            self.call_view = CallView(self)
            self.call_view.setGeometry(8, 8, self.width() - 16, self.height() - 16)
            self.call_view.hide()
            self.call_controller = None  # 通话时创建，避免闲置时持有 sounddevice stream

            # Dock 与输入框互斥切换的 opacity effect
            self._dock_opacity = QGraphicsOpacityEffect(self.dock_bar)
            self.dock_bar.setGraphicsEffect(self._dock_opacity)
            self._dock_opacity.setOpacity(1.0)

            self._input_opacity = QGraphicsOpacityEffect(self.input_panel)
            self.input_panel.setGraphicsEffect(self._input_opacity)
            self._input_opacity.setOpacity(0.0)

            # 历史抽屉（右侧滑入，默认隐藏）
            self.history_drawer = HistoryDrawer(self)
            self.history_drawer.setGeometry(self.width() - 172, 8, 168, self.height() - 80)
            self.history_drawer.hide()

            self._relayout()

            self.tray = QSystemTrayIcon(self)
            icon_pixmap = QPixmap(24, 24)
            icon_pixmap.fill(QColor(224, 82, 82))
            self.tray.setIcon(QIcon(icon_pixmap))
            tray_menu = QMenu()
            restore_action = tray_menu.addAction("显示红莉栖")
            focus_action = tray_menu.addAction("聚焦输入")
            quit_action = tray_menu.addAction("退出")
            restore_action.triggered.connect(self._restore_from_tray)
            focus_action.triggered.connect(self._focus_input)
            quit_action.triggered.connect(QApplication.quit)
            self.tray.setContextMenu(tray_menu)
            self.tray.activated.connect(self._tray_activated)
            self.tray.show()

            # 最小化时的悬浮恢复按钮（独立窗口）
            self._restore_win = QWidget()
            self._restore_win.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            self._restore_win.setFixedSize(50, 50)
            screen = QApplication.primaryScreen().availableGeometry()
            self._restore_win.move(screen.right() - 60, screen.bottom() - 120)
            btn = QPushButton("红利栖", self._restore_win)
            btn.setGeometry(0, 0, 50, 50)
            btn.setToolTip("点击打开")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{background:#d2738a;color:#171114;border:1px solid #d2738a;"
                "border-radius:0px;font:bold 11px 'Times New Roman','SimSun';padding:2px}"
                "QPushButton:hover{background:#c1b492}"
            )
            btn.clicked.connect(self._restore_from_tray)
            self._restore_win.hide()

            self.timer = QTimer(self)
            self.timer.timeout.connect(self.read_frames)
            self.timer.start(16)

            self.hotkey_timer = QTimer(self)
            self.hotkey_timer.timeout.connect(self._poll_global_hotkey)
            self.hotkey_timer.start(80)

            from PySide6.QtGui import QKeySequence, QShortcut
            # Ctrl+Space 已由 _poll_global_hotkey 的 win32api 全局轮询处理，
            # 此处不再注册 QShortcut，避免重复触发 _focus_input。
            QShortcut(QKeySequence("Escape"), self).activated.connect(
                self._hide_input_or_noop
            )

        def _focus_input(self) -> None:
            self._restore_from_tray()
            if not self.input_panel.isVisible():
                self._toggle_input_panel()
            self.input.setFocus()

        def _toggle_input_panel(self) -> None:
            """切换输入面板：Dock 淡出 + 输入框淡入，或反向。"""
            if self.input_panel.isVisible() and self._input_opacity.opacity() > 0.5:
                # 收起 input，恢复 dock 可点击
                self._cross_fade(self._input_opacity, self._dock_opacity)
                QTimer.singleShot(200, self.input_panel.hide)
                self.dock_bar.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            else:
                # 展开 input，dock 透明时不拦截鼠标（避免误点 dock 按钮）
                self.input_panel.show()
                self.input.setFocus()
                self._cross_fade(self._dock_opacity, self._input_opacity)
                self.dock_bar.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        def _cross_fade(self, fade_out_effect, fade_in_effect) -> None:
            """200ms opacity 交叉淡入淡出。"""
            anim_out = QPropertyAnimation(fade_out_effect, b"opacity", self)
            anim_out.setDuration(200)
            anim_out.setStartValue(fade_out_effect.opacity())
            anim_out.setEndValue(0.0)
            anim_out.setEasingCurve(QEasingCurve.OutCubic)
            anim_out.start()
            self._fade_out_anim = anim_out

            anim_in = QPropertyAnimation(fade_in_effect, b"opacity", self)
            anim_in.setDuration(200)
            anim_in.setStartValue(fade_in_effect.opacity())
            anim_in.setEndValue(1.0)
            anim_in.setEasingCurve(QEasingCurve.OutCubic)
            anim_in.start()
            self._fade_in_anim = anim_in

        def _toggle_pin(self) -> None:
            """固定/解锁位置。固定后禁用拖拽和自动贴合。DockButton 视觉反馈由 Task 5 处理。"""
            self._pinned = not self._pinned

        def _set_bubble_text(self, text: str) -> None:
            """设置回复气泡文字并自动缩放大小。"""
            self.reply_bubble.setText(text)
            from PySide6.QtGui import QFontMetrics
            fm = QFontMetrics(self.reply_bubble.font())
            max_w = 340
            rect = fm.boundingRect(0, 0, max_w - 36, 0, Qt.TextWordWrap, text)
            w = min(max(rect.width() + 36, 80), max_w)
            h = min(max(rect.height() + 24, 36), 140)
            x = (self.width() - w) // 2
            self.reply_bubble.setGeometry(x, 6, w, h)

        def _show_thinking_dots(self) -> None:
            """delta 期间显示思考动画（3 个青色点呼吸）。与 _show_next_bubble 共用 _bubble_opacity。"""
            self._set_bubble_text("● ● ●")
            if not hasattr(self, '_bubble_opacity'):
                self._bubble_opacity = QGraphicsOpacityEffect(self.reply_bubble)
                self.reply_bubble.setGraphicsEffect(self._bubble_opacity)
            if not hasattr(self, '_thinking_anim'):
                self._thinking_anim = QPropertyAnimation(self._bubble_opacity, b"opacity", self)
                self._thinking_anim.setDuration(1000)
                self._thinking_anim.setStartValue(0.4)
                self._thinking_anim.setKeyValueAt(0.5, 1.0)
                self._thinking_anim.setEndValue(0.4)
                self._thinking_anim.setLoopCount(-1)
            if self._thinking_anim.state() != QPropertyAnimation.Running:
                self._thinking_anim.start()

        def _send_emotion(self, emotion: str) -> None:
            """经 duplex 管道发送 emotion 到 renderer（即时，不等 LLM）。"""
            send_command(emotion=emotion)

        # ===== 通话态切换 =====
        def _toggle_call(self) -> None:
            """Dock 电话按钮：非通话态→进入通话，通话态→挂断。"""
            decision = _decide_call_toggle_action(self._in_call)
            if decision["enter_call"]:
                self._enter_call()
            elif decision["hangup"]:
                self._hangup_call()

        def _enter_call(self) -> None:
            """进入通话态：隐藏平时组件，显示 CallView，启动管线。"""
            config = load_config()
            if not all(config.get(key) for key in ("endpoint", "api_key", "model")):
                SettingsDialog(self).exec()
                return
            self._in_call = True
            self.reply_bubble.hide()
            self.input_panel.hide()
            self.history_drawer.hide()
            self.dock_bar.hide()
            self.call_view.show()
            self.call_view.raise_()
            self.call_view.setFocus()  # 确保 CallView 能接收键盘事件（Escape 退出）
            # 断开旧 controller 信号（避免重复连接）
            if self.call_controller is not None:
                try:
                    self.call_controller.phase_changed.disconnect()
                    self.call_controller.subtitle.disconnect()
                    self.call_controller.elapsed.disconnect()
                    self.call_controller.waveform.disconnect()
                    self.call_controller.you_said.disconnect()
                    self.call_controller.error.disconnect()
                except Exception:
                    pass
            # 重新创建 controller 以用最新 config
            from core.voice_call import VoiceCallController
            self.call_controller = VoiceCallController(config, character, self)
            self.call_controller.phase_changed.connect(self._on_call_phase_changed)
            self.call_controller.subtitle.connect(self.call_view.set_subtitle)
            self.call_controller.elapsed.connect(self.call_view.set_elapsed)
            self.call_controller.waveform.connect(self.call_view.set_waveform)
            self.call_controller.you_said.connect(self._on_call_you_said)
            self.call_controller.error.connect(self._on_call_error)
            # 断开 CallView 旧信号再重连（防止重复连接导致 hangup 被多次调用）
            try:
                self.call_view.mute_clicked.disconnect()
                self.call_view.hangup_clicked.disconnect()
                self.call_view.screen_clicked.disconnect()
            except Exception:
                pass
            self.call_view.mute_clicked.connect(self.call_controller.toggle_mute)
            self.call_view.hangup_clicked.connect(self._hangup_call)
            self.call_view.screen_clicked.connect(self.call_controller.toggle_screen_share)
            self.call_controller.start()

        def _hangup_call(self) -> None:
            """挂断：停管线，恢复平时态。即使 controller 为 None 也能恢复 UI。"""
            if not self._in_call:
                return
            self._in_call = False
            if self.call_controller is not None:
                try:
                    self.call_controller.hangup()
                except Exception:
                    pass
                try:
                    self.call_controller.phase_changed.disconnect()
                except Exception:
                    pass
                self.call_controller = None
            self.call_view.hide()
            self.dock_bar.show()
            self.reply_bubble.show()
            # 恢复对话气泡
            try:
                msgs = active_session(self._state)["messages"]
                if msgs:
                    self._set_bubble_text(self._latest_line(msgs[-1]["content"]))
            except Exception:
                pass

        def _on_call_phase_changed(self, phase: str) -> None:
            self.call_view.set_phase(phase)
            # Live2D 表情随状态切换
            emotion_map = {
                "listening": "neutral",
                "processing": "thinking",
                "speaking": "smile",
                "ended": "neutral",
            }
            emotion = emotion_map.get(phase)
            if emotion:
                self._send_emotion(emotion)
            # speaking 态驱动 Live2D 口型
            if phase == "speaking":
                send_command(speaking=True)
            elif phase in ("listening", "ended"):
                send_command(speaking=False)

        def _on_call_you_said(self, text: str) -> None:
            """通话中用户说的话（暂不显示，避免干扰红莉栖字幕）。"""
            pass

        def _on_call_error(self, text: str) -> None:
            self.call_view.set_subtitle(f"⚠ {text}")

        def _show_layered_bubbles(self, text: str) -> None:
            """将回复分层后分多个气泡前后展示，每段用 opacity 动画淡入。"""
            import re
            self._cancel_bubbles()
            # 停止思考呼吸动画，恢复 opacity
            if hasattr(self, '_thinking_anim') and self._thinking_anim.state() == QPropertyAnimation.Running:
                self._thinking_anim.stop()
                if hasattr(self, '_bubble_opacity'):
                    self._bubble_opacity.setOpacity(1.0)
            self._thinking_dots_shown = False
            segments = re.split(r'(?<=[。！？!?\n])\s*', text.strip())
            merged: list[str] = []
            for seg in segments:
                seg = seg.strip()
                if not seg:
                    continue
                if merged and len(merged[-1]) < 6:
                    merged[-1] += seg
                else:
                    merged.append(seg)
            if not merged:
                return
            self._bubble_segments = merged
            self._bubble_index = 0
            if not self._history_expanded:
                self.reply_bubble.show()
            self._show_next_bubble()

        def _show_next_bubble(self) -> None:
            """显示下一个气泡分段，用 opacity 动画淡入。"""
            if self._bubble_index >= len(self._bubble_segments):
                self._bubble_timer = QTimer.singleShot(9000, self._hide_idle_bubble)
                return
            text = self._bubble_segments[self._bubble_index]
            self._set_bubble_text(text)
            # opacity 淡入动画
            if not hasattr(self, '_bubble_opacity'):
                self._bubble_opacity = QGraphicsOpacityEffect(self.reply_bubble)
                self.reply_bubble.setGraphicsEffect(self._bubble_opacity)
            self._bubble_opacity.setOpacity(0.0)
            fade_in = QPropertyAnimation(self._bubble_opacity, b"opacity", self)
            fade_in.setDuration(180)
            fade_in.setStartValue(0.0)
            fade_in.setEndValue(1.0)
            fade_in.setEasingCurve(QEasingCurve.OutCubic)
            fade_in.start()
            self._fade_anim = fade_in  # 保持引用防 GC
            if not self._history_expanded:
                self.reply_bubble.show()
            char_count = len(text)
            duration = int(min(1500 + char_count * 80, 6000))
            self._bubble_index += 1
            self._bubble_timer = QTimer.singleShot(duration, self._show_next_bubble)

        def _cancel_bubbles(self) -> None:
            """取消正在进行的气泡序列。"""
            if self._bubble_timer is not None:
                self._bubble_timer.stop()
                self._bubble_timer = None
            self._bubble_segments = []
            self._bubble_index = 0

        def _notify_tts_offline(self) -> None:
            """语音服务离线提示：在当前气泡序列末尾追加一条，不打断展示。"""
            notice = "（语音服务离线）"
            segments = getattr(self, "_bubble_segments", [])
            if notice not in segments:
                self._bubble_segments = segments + [notice]

        def _animate_to(self, target: QPoint) -> None:
            """平滑滑动到目标位置（300ms OutCubic 缓动）。"""
            if self._snap_anim is not None:
                self._snap_anim.stop()
            self._snap_anim = QPropertyAnimation(self, b"pos")
            self._snap_anim.setDuration(300)
            self._snap_anim.setStartValue(self.pos())
            self._snap_anim.setEndValue(target)
            self._snap_anim.setEasingCurve(QEasingCurve.OutCubic)
            self._snap_anim.start()

        def _restore_from_tray(self) -> None:
            self._restore_win.hide()
            self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()

        def _minimize_to_tray(self) -> None:
            self.hide()
            self._restore_win.show()

        def _tray_activated(self, reason) -> None:
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
                self._restore_from_tray()

        @staticmethod
        def _latest_line(text: str) -> str:
            chinese = parse_reply(text).chinese
            lines = [line.strip() for line in chinese.splitlines() if line.strip()]
            latest = " ".join(lines[-3:]) if lines else "…"
            return latest if len(latest) <= 105 else latest[:104] + "…"

        def _render_history(self) -> None:
            blocks = []
            for message in active_session(self._state)["messages"]:
                assistant = message["role"] == "assistant"
                text = parse_reply(message["content"]).chinese if assistant else message["content"]
                if assistant:
                    blocks.append(_build_kurisu_html(text))
                else:
                    blocks.append(_build_you_html(text))
            self.history_drawer.set_messages_html("".join(blocks))

        def _toggle_history(self) -> None:
            self._history_expanded = not self._history_expanded
            if self._history_expanded:
                self._render_history()
                self.history_drawer.show()
                self.history_drawer.slide_in()
                self.input_panel.hide()
                self.reply_bubble.hide()
            else:
                self.history_drawer.slide_out()
                QTimer.singleShot(300, self.history_drawer.hide)

        def _hide_input_or_noop(self) -> None:
            """Escape 键：收起输入面板（若已展开），Dock 淡入恢复。"""
            if self.input_panel.isVisible() and self._input_opacity.opacity() > 0.5:
                self._toggle_input_panel()

        def _poll_global_hotkey(self) -> None:
            try:
                import win32api
            except ImportError:
                return  # 非 Windows 平台，跳过全局热键
            # Ctrl+Space → 聚焦输入
            cs_pressed = bool(win32api.GetAsyncKeyState(0x11) & 0x8000) and bool(win32api.GetAsyncKeyState(0x20) & 0x8000)
            if cs_pressed and not self._hotkey_down:
                self._focus_input()
            self._hotkey_down = cs_pressed
            # Ctrl+Alt+S → 切换显示/隐藏
            cas_pressed = (
                bool(win32api.GetAsyncKeyState(0x11) & 0x8000)
                and bool(win32api.GetAsyncKeyState(0x12) & 0x8000)
                and bool(win32api.GetAsyncKeyState(0x53) & 0x8000)
            )
            if cas_pressed and not self._csa_down:
                self._toggle_visibility()
            self._csa_down = cas_pressed

        def _toggle_visibility(self) -> None:
            """Ctrl+Alt+S 切换桌宠显示/隐藏。"""
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.raise_()
                self.activateWindow()

        def _send(self) -> None:
            text = self.input.text().strip()
            if not text or self._busy:
                return
            config = load_config()
            if not all(config.get(key) for key in ("endpoint", "api_key", "model")):
                SettingsDialog(self).exec()
                return
            self.input.clear()
            self._cancel_bubbles()
            self.reply_bubble.show()
            session = active_session(self._state)
            add_message(session, "user", text)
            instant = _decide_send_instant_action()
            self._show_thinking_dots()
            self._send_emotion(instant["emotion"])
            self._busy = True
            self._streamed_reply = ""
            # 流式 TTS 状态：_stream_japanese_started 标记已进入日语段（=== 之后）
            self._stream_japanese_started = False
            self.send_button.setDisabled(True)
            history = [{"role": message["role"], "content": message["content"]} for message in session["messages"][-14:]]
            task = AgentTask(history, session.get("memories", []))
            task.signals.status.connect(self._show_status)
            task.signals.delta.connect(self._agent_delta)
            task.signals.finished.connect(self._agent_finished)
            task.signals.failed.connect(self._agent_failed)
            task.signals.confirmation.connect(self._confirm_operation)
            QThreadPool.globalInstance().start(task)

        def _confirm_operation(self, request: dict) -> None:
            payload = request["payload"]
            command = payload.get("command", "")
            description = payload.get("description", "")
            detail_text = description or command
            if not detail_text:
                detail_text = __import__("json").dumps(payload, ensure_ascii=False, indent=2)
            details = html.escape(detail_text)
            msg = QMessageBox(self)
            msg.setWindowTitle("红莉栖请求操作电脑")
            msg.setIcon(QMessageBox.Question)
            msg.setText(f"她要执行：\n{details}\n\n允许吗？")
            btn_once = msg.addButton("仅本次", QMessageBox.AcceptRole)
            btn_session = msg.addButton("本次会话", QMessageBox.AcceptRole)
            btn_always = msg.addButton("始终允许", QMessageBox.AcceptRole)
            btn_deny = msg.addButton("拒绝", QMessageBox.RejectRole)
            msg.setDefaultButton(btn_deny)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == btn_once:
                request["choice"] = "once"
            elif clicked == btn_session:
                request["choice"] = "session"
            elif clicked == btn_always:
                request["choice"] = "always"
            else:
                request["choice"] = "deny"
            request["event"].set()

        def _show_status(self, text: str) -> None:
            if not self._history_expanded:
                self.reply_bubble.show()
            self._set_bubble_text(self._latest_line(text))

        def _agent_delta(self, text: str) -> None:
            new_streamed, should_show_thinking, should_set_bubble_text = _decide_delta_action(
                self._streamed_reply, text, self._history_expanded
            )
            self._streamed_reply = new_streamed
            if should_set_bubble_text:
                self._set_bubble_text(self._streamed_reply)
            if should_show_thinking:
                self._show_thinking_dots()
            # 流式 TTS：检测 === 分隔符，进入日语段后增量追加到 SpeechPlayer
            config = load_config()
            if config.get("tts_enabled", True):
                if not self._stream_japanese_started:
                    # 检测是否出现 === 分隔符（中日文分界）
                    if "===" in new_streamed:
                        self._stream_japanese_started = True
                        # 启动流式合成会话
                        self.speech.set_rate([-2, 0, 2][config.get("tts_rate", 1)])
                        self.speech.speak_streaming_start(text_lang="ja")
                        # 提取 === 之后的日语部分追加
                        jp_part = new_streamed.split("===", 1)[1].lstrip("=\r\n").strip()
                        if jp_part:
                            self.speech.speak_streaming_append(jp_part)
                else:
                    # 已在日语段，增量追加（去掉可能残留的 ===）
                    if "===" in text:
                        text = text.split("===", 1)[-1].lstrip("=\r\n")
                    if text:
                        self.speech.speak_streaming_append(text)

        def _agent_finished(self, reply: str) -> None:
            session = active_session(self._state)
            add_message(session, "assistant", reply)
            save_state(character.id, self._state)
            self._render_history()
            self._busy = False
            self._streamed_reply = ""
            self.send_button.setDisabled(False)
            parsed = parse_reply(reply)
            send_command(emotion=parsed.emotion)
            # 流式 TTS：会话结束，刷新剩余缓冲
            # （流式合成在 _agent_delta 中已开始，这里只刷新剩余文本）
            config = load_config()
            if config.get("tts_enabled", True) and self._stream_japanese_started:
                self.speech.speak_streaming_end()
            elif config.get("tts_enabled", True) and parsed.japanese and not self._stream_japanese_started:
                # 兜底：如果流式未启动（如无 === 分隔符），整段合成
                self.speech.set_rate([-2, 0, 2][config.get("tts_rate", 1)])
                self.speech.speak_with_options(
                    parsed.japanese,
                    text_lang="ja",
                    allow_fallback=False,
                )
            self._stream_japanese_started = False
            if not self._history_expanded:
                self._show_layered_bubbles(self._latest_line(reply))

        def _hide_idle_bubble(self) -> None:
            if not self._busy and not self._history_expanded:
                self.reply_bubble.hide()

        def _agent_failed(self, error: str) -> None:
            self._set_bubble_text(self._latest_line(f"任务失败：{error}"))
            self._busy = False
            self._streamed_reply = ""
            self.send_button.setDisabled(False)
            send_command(emotion="angry")

        def read_frames(self) -> None:
            latest = None
            while connection.poll():
                kind, payload = connection.recv()
                if kind == "frame":
                    latest = payload
                elif kind == "error":
                    print(f"[Amadeus] {payload}", file=sys.stderr)
            if latest is not None:
                image = QImage.fromData(latest, "PNG")
                if not image.isNull():
                    self._frame = image
                    if not self._first_frame_received:
                        self._first_frame_received = True
                        image.save(str(ROOT / "data" / "received-frame.png"), "PNG")
                        READY_FILE.parent.mkdir(parents=True, exist_ok=True)
                        READY_FILE.write_text("KURISU_READY", encoding="ascii")
                    self.update()

        def paintEvent(self, event) -> None:
            if self._frame.isNull():
                return
            painter = QPainter(self)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            # 全身显示，按 _zoom 缩放，水平居中，底部对齐
            base_h = 520
            target_h = int(base_h * self._zoom)
            target_w = int(target_h * self._frame.width() / self._frame.height())
            scaled = self._frame.scaled(
                target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            target = QRect(
                (self.width() - scaled.width()) // 2,
                self.height() - scaled.height() - 60,
                scaled.width(), scaled.height(),
            )
            painter.drawImage(target, scaled)

        def wheelEvent(self, event) -> None:
            if self._pinned:
                return
            delta = event.angleDelta().y() / 120
            new_zoom = self._zoom + delta * 0.1
            self._zoom = max(0.5, min(2.0, new_zoom))
            self.update()

        def eventFilter(self, obj, event) -> bool:
            return super().eventFilter(obj, event)

        def _relayout(self) -> None:
            """根据当前窗口尺寸重新定位所有组件。"""
            w, h = self.width(), self.height()
            # Dock：底部居中悬浮
            dock_w = self.dock_bar.sizeHint().width()
            self.dock_bar.setGeometry((w - dock_w) // 2, h - 56, dock_w, 48)
            # 输入面板：底部居中（与 Dock 同位，互斥显示）
            panel_w = 320
            self.input_panel.setGeometry((w - panel_w) // 2, h - 56, panel_w, 48)
            # 历史抽屉：右侧
            self.history_drawer.setGeometry(w - 172, 8, 168, h - 80)

        def resizeEvent(self, event) -> None:
            self._relayout()
            super().resizeEvent(event)

        def keyPressEvent(self, event: QKeyEvent) -> None:
            if event.key() == Qt.Key_Escape and self._in_call:
                self._hangup_call()
                return
            super().keyPressEvent(event)

        def mousePressEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.LeftButton:
                # input 可见时点 PetWindow 空白区收起 input，不触发拖拽/聚焦
                if self.input_panel.isVisible() and self._input_opacity.opacity() > 0.5:
                    self._toggle_input_panel()
                    return
                if event.position().x() > 50:
                    self._focus_input()
                if not self._pinned:
                    self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            elif event.button() == Qt.RightButton:
                menu = QMenu(self)
                quit_action = menu.addAction("退出桌宠")
                if menu.exec(event.globalPosition().toPoint()) is quit_action:
                    QApplication.quit()

        def mouseMoveEvent(self, event: QMouseEvent) -> None:
            if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag_offset)

        def mouseReleaseEvent(self, event: QMouseEvent) -> None:
            if self._drag_offset is not None:
                self._user_pos = self.pos()  # 记住用户拖拽后的位置
                self._was_desktop = True
            self._drag_offset = None

        def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.LeftButton and event.position().x() > self.width() // 2:
                self._toggle_history()
                event.accept()

    app = QApplication(sys.argv)
    app.setApplicationName("Amadeus Kurisu")
    _ensure_dither_texture()  # fauux 抖动纹理（幂等，缺失时生成）
    pet = PetWindow()
    app.aboutToQuit.connect(lambda: renderer.terminate() if renderer.is_alive() else None)
    pet.show()
    pet.raise_()
    if os.environ.get("AMADEUS_UI_SNAPSHOT"):
        def save_snapshot() -> None:
            if os.environ.get("AMADEUS_UI_SNAPSHOT") == "history":
                pet._toggle_history()
            elif os.environ.get("AMADEUS_UI_SNAPSHOT") == "bubble":
                pet._set_bubble_text("我已经理解你的任务。接下来会先检查当前桌面状态，\n再执行必要操作，并把最终结果告诉你。")
                pet.reply_bubble.show()
            pet.grab().save(str(ROOT / "data" / "ui-snapshot.png"), "PNG")
            app.quit()
        QTimer.singleShot(7000, save_snapshot)
    return app.exec()


def main() -> int:
    mp.freeze_support()
    # 语音服务随主进程自启：不在线则后台拉起 GPT-SoVITS API
    # （失败不阻塞桌宠，SpeechPlayer 的 TTL 重查 + 离线气泡提示兜底）
    try:
        maybe_start_gpt_sovits()
    except Exception:
        pass
    READY_FILE.unlink(missing_ok=True)
    parent_connection, child_connection = mp.Pipe(duplex=True)
    renderer = mp.Process(target=renderer_process, args=(child_connection,), daemon=True)
    renderer.start()
    try:
        return run_overlay(parent_connection, renderer)
    finally:
        if renderer.is_alive():
            renderer.terminate()
        renderer.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
