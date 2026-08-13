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
import sys
import threading
import time


ROOT = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
# 通信文件必须用 storage.APP_DIR（exe 同目录/data/），与 pet_controller.py 保持一致；
# 不能用 ROOT/data，因为冻结模式下 ROOT=sys._MEIPASS 是临时解压目录，会与发送端失联。
from core.storage import APP_DIR as _APP_DIR
READY_FILE = _APP_DIR / "desktop_pet.ready"
COMMAND_FILE = _APP_DIR / "pet_command.json"


class QuietHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, format, *args):
        pass


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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


def run_overlay(connection: Connection, renderer: mp.Process) -> int:
    from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPropertyAnimation, QRect, QRunnable, Qt, QThreadPool, QTimer, Signal
    from PySide6.QtGui import QColor, QIcon, QImage, QMouseEvent, QPainter, QPixmap
    from PySide6.QtWidgets import (
                QApplication, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                QPushButton, QSystemTrayIcon, QTextBrowser, QVBoxLayout, QWidget,
                QGraphicsOpacityEffect,
            )

    from config import HERMES_DEFAULTS, KURISU_OUTPUT_FORMAT, get_character_by_id, get_random_greeting
    from core.agent_client import _load_soul_md, run_local_run
    from core.emotion_parser import parse_reply
    from core.pet_controller import send_pet_command
    from core.session_manager import active_session, add_message, load_state, save_state
    from core.storage import load_config
    from core.tts_client import SpeechPlayer
    from ui.settings_dialog import SettingsDialog

    character = get_character_by_id("kurisu")

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
                reply = run_local_run(
                    endpoint=config.get("endpoint", ""),
                    api_key=config.get("api_key", ""),
                    model=config.get("model", ""),
                    soul_md=soul_md,
                    instructions=KURISU_OUTPUT_FORMAT,
                    input_text=self.history[-1]["content"],
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

    class HistoryPhonePanel(QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self._phone = QImage(str(ROOT / "resources" / "images" / "mail_phone_frame.png"))

        def paintEvent(self, event) -> None:
            if self._phone.isNull():
                return
            painter = QPainter(self)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawImage(self.rect(), self._phone)

    class MessageInputPanel(QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self._left = QImage(str(ROOT / "resources" / "images" / "meswinLeft.png"))
            self._right = QImage(str(ROOT / "resources" / "images" / "meswinRight.png"))

        def paintEvent(self, event) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.fillRect(self.rect(), QColor(3, 3, 5, 214))
            painter.fillRect(QRect(8, 8, self.width() - 16, self.height() - 16), QColor(8, 8, 11, 218))
            if not self._left.isNull():
                painter.drawImage(QRect(0, 0, 168, self.height()), self._left)
            if not self._right.isNull():
                painter.drawImage(QRect(self.width() - 168, 0, 168, self.height()), self._right)

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
            self._zoom = 0.75
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
            self.speech.speaking_changed.connect(lambda value: send_pet_command(speaking=value))
            self.setWindowTitle("牧濑红莉栖 [PY]")
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_NoSystemBackground, True)
            self.setFixedSize(400, 680)

            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.right() - self.width() - 20, screen.bottom() - self.height())

            self.reply_bubble = QLabel(self)
            self.reply_bubble.setGeometry((self.width() - 390) // 2, 8, 390, 96)
            self.reply_bubble.setAlignment(Qt.AlignCenter)
            self.reply_bubble.setWordWrap(True)
            self.reply_bubble.setStyleSheet(
                "QLabel{background:rgba(255,255,255,245);color:#1d1d1f;"
                "border:1px solid rgba(0,0,0,0.06);border-radius:22px;"
                "padding:12px 18px;font:14px 'Segoe UI','Microsoft YaHei';"
                "font-weight:400;letter-spacing:0.2px}"
            )
            self._set_bubble_text(self._latest_line(active_session(self._state)["messages"][-1]["content"]))
            self.reply_bubble.hide()

            self.history_panel = HistoryPhonePanel(self)
            self.history_panel.setGeometry(6, self.height() - 465, 300, 400)

            screen_frame = QWidget(self.history_panel)
            screen_frame.setGeometry(35, 120, 230, 200)
            screen_frame.setStyleSheet("background:transparent;border:0")

            self.history = QTextBrowser(screen_frame)
            self.history.setGeometry(0, 0, 230, 200)
            self.history.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
            self.history.setStyleSheet(
                "QTextBrowser{background:transparent;color:#101014;border:0;padding:8px 14px 8px 9px;"
                "font:12px 'Lucida Console','Consolas','SimSun'}"
                "QScrollBar:vertical{background:#d8d8d2;width:6px;margin:8px 10px 8px 0}"
                "QScrollBar::handle:vertical{background:#17171b;border-radius:0;min-height:30px}"
                "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;background:transparent}"
                "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:#d8d8d2}"
            )
            self.history.setOpenExternalLinks(False)

            self.history_panel.hide()
            self._render_history()

            # 竖排工具栏（角色左侧，参考 ANISpace Live2D Widget）
            self.tool_bar = QWidget(self)
            self.tool_bar.setGeometry(20, self.height() // 2 - 110, 44, 220)
            tool_layout = QVBoxLayout(self.tool_bar)
            tool_layout.setContentsMargins(0, 0, 0, 0)
            tool_layout.setSpacing(6)
            tool_layout.setAlignment(Qt.AlignCenter)

            _tool_btn_style = (
                "QPushButton{background:rgba(255,255,255,180);color:#8e8e93;border:1px solid rgba(0,0,0,0.06);"
                "border-radius:10px;font-size:16px}"
                "QPushButton:hover{background:#007AFF;color:white;border:1px solid #007AFF}"
            )
            _quit_btn_style = (
                "QPushButton{background:rgba(255,255,255,180);color:#8e8e93;border:1px solid rgba(0,0,0,0.06);"
                "border-radius:10px;font-size:16px}"
                "QPushButton:hover{background:#ff3b30;color:white;border:1px solid #ff3b30}"
            )

            chat_button = QPushButton("💬")
            chat_button.setToolTip("对话")
            chat_button.clicked.connect(self._toggle_input_panel)
            pin_button = QPushButton("📌")
            pin_button.setToolTip("固定位置")
            pin_button.clicked.connect(self._toggle_pin)
            settings_button = QPushButton("⚙")
            settings_button.setToolTip("设置")
            settings_button.clicked.connect(lambda: SettingsDialog(self).exec())
            history_button = QPushButton("☰")
            history_button.setToolTip("记录")
            history_button.clicked.connect(self._toggle_history)
            minimize_button = QPushButton("−")
            minimize_button.setToolTip("最小化")
            minimize_button.clicked.connect(self._minimize_to_tray)
            close_button_tool = QPushButton("×")
            close_button_tool.setToolTip("退出")
            close_button_tool.clicked.connect(QApplication.quit)

            for btn in (chat_button, pin_button, settings_button, history_button, minimize_button):
                btn.setFixedSize(36, 36)
                btn.setStyleSheet(_tool_btn_style)
                tool_layout.addWidget(btn)
            close_button_tool.setFixedSize(36, 36)
            close_button_tool.setStyleSheet(_quit_btn_style)
            tool_layout.addWidget(close_button_tool)
            self.chat_button = chat_button
            self.pin_button = pin_button

            # 工具栏悬浮淡入效果（默认半透明，悬浮时全显）
            self._tool_opacity = QGraphicsOpacityEffect(self.tool_bar)
            self._tool_opacity.setOpacity(0.35)
            self.tool_bar.setGraphicsEffect(self._tool_opacity)
            self.tool_bar.installEventFilter(self)

            # 输入面板（默认隐藏，点击💬展开）
            panel_w = 365
            panel_x = (self.width() - panel_w) // 2
            self.input_panel = QWidget(self)
            self.input_panel.setGeometry(panel_x, self.height() - 70, panel_w, 52)
            self.input_panel.setStyleSheet(
                "background:rgba(255,255,255,230);"
                "border:1px solid rgba(0,0,0,0.08);border-radius:24px"
            )
            input_layout = QHBoxLayout(self.input_panel)
            input_layout.setContentsMargins(14, 6, 6, 6)
            input_layout.setSpacing(4)
            self.input = QLineEdit()
            self.input.setPlaceholderText("和红莉栖对话…")
            self.input.setStyleSheet(
                "QLineEdit{background:transparent;color:#1d1d1f;border:0;padding:8px 10px;"
                "font-size:14px;font-family:'Segoe UI','Microsoft YaHei'}"
                "QLineEdit::placeholder{color:#8e8e93}"
            )
            self.input.returnPressed.connect(self._send)
            input_layout.addWidget(self.input, 1)
            send_button = QPushButton("↑")
            send_button.setToolTip("发送")
            send_button.clicked.connect(self._send)
            send_button.setFixedSize(36, 36)
            send_button.setStyleSheet(
                "QPushButton{background:#007AFF;color:white;border:0;border-radius:18px;font-size:16px;font-weight:bold}"
                "QPushButton:hover{background:#0066d6} QPushButton:disabled{background:#b0d4ff}"
            )
            input_layout.addWidget(send_button)
            self.send_button = send_button
            self.input_panel.hide()

            self.close_button = QPushButton("×", self)
            self.close_button.setToolTip("关闭桌宠")
            self.close_button.setGeometry(self.width() - 36, 12, 26, 26)
            self.close_button.setStyleSheet(
                "QPushButton{background:rgba(255,255,255,200);color:#8e8e93;border:1px solid rgba(0,0,0,0.06);"
                "border-radius:13px;font-size:16px}"
                "QPushButton:hover{background:#ff3b30;color:white}"
            )
            self.close_button.clicked.connect(QApplication.quit)
            self.close_button.hide()

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
                "QPushButton{background:#007AFF;color:white;border:none;"
                "border-radius:25px;font:bold 11px 'Segoe UI','Microsoft YaHei';padding:2px}"
                "QPushButton:hover{background:#0051D5}"
            )
            btn.clicked.connect(self._restore_from_tray)
            self._restore_win.hide()

            self.timer = QTimer(self)
            self.timer.timeout.connect(self.read_frames)
            self.timer.start(16)

            self.hotkey_timer = QTimer(self)
            self.hotkey_timer.timeout.connect(self._poll_global_hotkey)
            self.hotkey_timer.start(80)

            # 前台窗口检测：非桌面场景自动贴合右下角，桌面场景恢复用户位置
            self._auto_hidden = False
            self._foreground_timer = QTimer(self)
            self._foreground_timer.timeout.connect(self._check_foreground)
            self._foreground_timer.start(500)

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
            """切换输入面板可见性。"""
            if self.input_panel.isVisible():
                self.input_panel.hide()
            else:
                self.input_panel.show()
                self.input.setFocus()

        def _toggle_pin(self) -> None:
            """固定/解锁位置。固定后禁用拖拽和自动贴合。"""
            self._pinned = not self._pinned
            if self._pinned:
                self.pin_button.setStyleSheet(
                    "QPushButton{background:#007AFF;color:white;border:1px solid #007AFF;"
                    "border-radius:10px;font-size:16px}"
                )
                self.pin_button.setToolTip("已固定，点击解锁")
            else:
                self.pin_button.setStyleSheet(
                    "QPushButton{background:rgba(255,255,255,180);color:#8e8e93;border:1px solid rgba(0,0,0,0.06);"
                    "border-radius:10px;font-size:16px}"
                    "QPushButton:hover{background:#007AFF;color:white;border:1px solid #007AFF}"
                )
                self.pin_button.setToolTip("固定位置")

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
            """delta 期间显示思考动画（3 个青色点呼吸），不显示流式文字。"""
            if not hasattr(self, '_thinking_dots_shown'):
                self._set_bubble_text("● ● ●")
                self._thinking_dots_shown = True

        def _show_layered_bubbles(self, text: str) -> None:
            """将回复分层后分多个气泡前后展示，每段用 opacity 动画淡入。"""
            import re
            self._cancel_bubbles()
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
                safe_text = html.escape(text).replace("\n", "<br>")
                if assistant:
                    blocks.append(
                        "<div style='margin:0 0 13px 0'>"
                        "<div style='color:#3c5f9f;font-weight:bold'>Kurisu</div>"
                        "<div style='margin-top:2px;line-height:1.42;color:#111'>"
                        f"{safe_text}</div></div>"
                    )
                else:
                    blocks.append(
                        "<div style='margin:0 0 13px 0;text-align:right'>"
                        "<div style='color:#b14545;font-weight:bold'>You</div>"
                        "<div style='margin-top:2px;line-height:1.42;color:#111'>"
                        f"{safe_text}</div></div>"
                    )
            self.history.setHtml(
                "<html><body style=\"margin:0;background:#fbfbf8;font-family:'Lucida Console','Consolas','SimSun';"
                "font-size:13px;letter-spacing:0;color:#111\">"
                + "".join(blocks)
                + "</body></html>"
            )
            self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())

        def _set_history_visible(self, visible: bool) -> None:
            self._history_expanded = visible
            self.history_panel.setVisible(visible)
            if visible:
                self.input_panel.hide()
                self.reply_bubble.hide()

        def _toggle_history(self) -> None:
            self._set_history_visible(not self._history_expanded)

        def _hide_input_or_noop(self) -> None:
            """Escape 键：收起输入面板（若已展开）。"""
            if self.input_panel.isVisible():
                self.input_panel.hide()

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

        def _check_foreground(self) -> None:
            """检测前台窗口：非桌面场景自动贴合右下角，桌面场景恢复用户位置。

            规则（与 project_memory 约定一致）：
            - _pinned 为 True 时，不做任何自动位移
            - 前台是桌面（GetForegroundWindow 返回 0 或标题为 Program Manager）→ 恢复 _user_pos
            - 前台是非桌面窗口 → 平滑滑到右下角
            """
            if self._pinned:
                return
            try:
                import win32gui
            except ImportError:
                return
            try:
                foreground = win32gui.GetForegroundWindow()
            except Exception:
                return
            # 前台是自己时不动作，避免拖拽中被强行拉走
            if foreground == int(self.winId()):
                return
            title = win32gui.GetWindowText(foreground).strip() if foreground else ""
            is_desktop = (foreground == 0) or (title == "Program Manager")
            screen = QApplication.primaryScreen().availableGeometry()
            if is_desktop:
                # 桌面场景：回到用户上次拖拽位置，没有则保持当前
                if self._user_pos is not None and self.pos() != self._user_pos:
                    self._animate_to(self._user_pos)
                self._auto_hidden = False
            else:
                # 非桌面场景：滑到右下角（仅当当前不在右下角附近时才动）
                target = QPoint(
                    screen.right() - self.width() - 20,
                    screen.bottom() - self.height() + 20,  # 略微下沉，露出头部
                )
                # 阈值 30px，避免抖动
                if (self.pos() - target).manhattanLength() > 30:
                    self._animate_to(target)
                    self._auto_hidden = True

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
            if self._auto_hidden:
                self._inactivity_timer.start(20000)
            self.reply_bubble.show()
            session = active_session(self._state)
            add_message(session, "user", text)
            self._set_bubble_text("让我想想…")
            self._busy = True
            self._streamed_reply = ""
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

        def _agent_finished(self, reply: str) -> None:
            session = active_session(self._state)
            add_message(session, "assistant", reply)
            save_state(character.id, self._state)
            self._render_history()
            self._busy = False
            self._streamed_reply = ""
            self.send_button.setDisabled(False)
            parsed = parse_reply(reply)
            send_pet_command(emotion=parsed.emotion)
            # TTS：朗读日语部分（与 chat_window.py 行为一致）
            config = load_config()
            if config.get("tts_enabled", True) and parsed.japanese:
                self.speech.set_rate([-2, 0, 2][config.get("tts_rate", 1)])
                self.speech.speak(parsed.japanese)
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
            send_pet_command(emotion="angry")

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
            # 全身显示，按 _zoom 缩放，水平居中偏右（给工具栏留空间），底部对齐
            base_h = 520
            target_h = int(base_h * self._zoom)
            target_w = int(target_h * self._frame.width() / self._frame.height())
            scaled = self._frame.scaled(
                target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            target = QRect(
                (self.width() - scaled.width()) // 2 + 15,
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
            """工具栏悬浮淡入淡出。"""
            if obj is self.tool_bar:
                if event.type() == event.Type.Enter:
                    self._tool_opacity.setOpacity(1.0)
                elif event.type() == event.Type.Leave:
                    self._tool_opacity.setOpacity(0.35)
            return super().eventFilter(obj, event)

        def _relayout(self) -> None:
            """根据当前窗口尺寸重新定位所有组件，紧贴人物。"""
            w, h = self.width(), self.height()
            # 回复气泡：由 _set_bubble_text 自动管理尺寸和位置
            # 工具栏：左侧紧贴人物
            self.tool_bar.setGeometry(6, (h - 250) // 2, 40, 250)
            # 历史面板：覆盖左侧区域
            hist_h = min(400, h - 130)
            self.history_panel.setGeometry(6, h - hist_h - 65, 300, hist_h)
            # 输入面板：底部居中
            panel_w = 320
            self.input_panel.setGeometry((w - panel_w) // 2, h - 58, panel_w, 48)
            # 关闭按钮：右上角
            self.close_button.setGeometry(w - 28, 6, 22, 22)

        def resizeEvent(self, event) -> None:
            self._relayout()
            super().resizeEvent(event)

        def mousePressEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.LeftButton:
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
    pet = PetWindow()
    app.aboutToQuit.connect(lambda: renderer.terminate() if renderer.is_alive() else None)
    pet.show()
    pet.raise_()
    if os.environ.get("AMADEUS_UI_SNAPSHOT"):
        def save_snapshot() -> None:
            if os.environ.get("AMADEUS_UI_SNAPSHOT") == "history":
                pet._set_history_visible(True)
            elif os.environ.get("AMADEUS_UI_SNAPSHOT") == "bubble":
                pet._set_bubble_text("我已经理解你的任务。接下来会先检查当前桌面状态，\n再执行必要操作，并把最终结果告诉你。")
                pet.reply_bubble.show()
            pet.grab().save(str(ROOT / "data" / "ui-snapshot.png"), "PNG")
            app.quit()
        QTimer.singleShot(7000, save_snapshot)
    return app.exec()


def main() -> int:
    mp.freeze_support()
    READY_FILE.unlink(missing_ok=True)
    parent_connection, child_connection = mp.Pipe(duplex=False)
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
