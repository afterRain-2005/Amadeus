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


ROOT = Path(__file__).resolve().parent
READY_FILE = ROOT / "data" / "desktop_pet.ready"
COMMAND_FILE = ROOT / "data" / "pet_command.json"


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


def run_overlay(connection: Connection, renderer: mp.Process) -> int:
    from PySide6.QtCore import QObject, QPoint, QRect, QRunnable, Qt, QThreadPool, QTimer, Signal
    from PySide6.QtGui import QColor, QIcon, QImage, QMouseEvent, QPainter, QPixmap
    from PySide6.QtWidgets import (
        QApplication, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
        QPushButton, QSystemTrayIcon, QTextBrowser, QVBoxLayout, QWidget,
    )

    from config import get_character_by_id, get_random_greeting
    from core.agent_client import run_agent
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
        def __init__(self, history, memories) -> None:
            super().__init__()
            self.history = history
            self.memories = memories
            self.signals = AgentSignals()

        def run(self) -> None:
            config = load_config()
            try:
                reply = run_agent(
                    endpoint=config["endpoint"], api_key=config["api_key"], model=config["model"],
                    personality=character.personality, history=self.history, memories=self.memories,
                    on_status=self.signals.status.emit, on_delta=self.signals.delta.emit,
                    confirm=self._confirm,
                )
                self.signals.finished.emit(reply)
            except Exception as exc:
                self.signals.failed.emit(str(exc))

        def _confirm(self, name: str, arguments: dict) -> bool:
            import threading
            request = {"name": name, "arguments": arguments, "event": threading.Event(), "allowed": False}
            self.signals.confirmation.emit(request)
            request["event"].wait()
            return request["allowed"]

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
            self._history_expanded = False
            self._state = load_state(character.id, get_random_greeting(character.id))
            self.speech = SpeechPlayer(self)
            self.speech.speaking_changed.connect(lambda value: send_pet_command(speaking=value))
            self.setWindowTitle("牧濑红莉栖 [PY]")
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_NoSystemBackground, True)
            self.setFixedSize(780, 760)

            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.right() - self.width() - 20, screen.bottom() - self.height())

            self.reply_bubble = QLabel(self)
            self.reply_bubble.setGeometry(365, 8, 390, 96)
            self.reply_bubble.setAlignment(Qt.AlignCenter)
            self.reply_bubble.setWordWrap(True)
            self.reply_bubble.setStyleSheet(
                "QLabel{background:rgba(255,255,255,238);color:#54545a;"
                "border:1px solid rgba(232,232,237,210);border-radius:16px;"
                "padding:10px 16px;font:13px 'Microsoft YaHei'}"
            )
            self.reply_bubble.setText(self._latest_line(active_session(self._state)["messages"][-1]["content"]))
            self.reply_bubble.hide()

            self.history_panel = HistoryPhonePanel(self)
            self.history_panel.setGeometry(12, self.height() - 500, 357, 500)

            screen_frame = QWidget(self.history_panel)
            screen_frame.setGeometry(49, 154, 252, 241)
            screen_frame.setStyleSheet("background:transparent;border:0")

            self.history = QTextBrowser(screen_frame)
            self.history.setGeometry(0, 0, 252, 241)
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

            self.input_panel = QWidget(self)
            self.input_panel.setGeometry(390, 690, 365, 48)
            self.input_panel.setStyleSheet(
                "background:rgba(245,240,242,238);"
                "border:1px solid rgba(232,232,237,210);border-radius:24px"
            )
            input_layout = QHBoxLayout(self.input_panel)
            input_layout.setContentsMargins(8, 6, 8, 6)
            self.input = QLineEdit()
            self.input.setPlaceholderText("和红莉栖对话，或交给她一个任务…")
            self.input.setStyleSheet(
                "QLineEdit{background:transparent;color:#1d1d1f;border:0;padding:6px 8px;font-size:13px}"
                "QLineEdit::placeholder{color:#aeaeb2}"
            )
            self.input.returnPressed.connect(self._send)
            input_layout.addWidget(self.input, 1)
            settings_button = QPushButton("⚙")
            settings_button.setToolTip("语音设置")
            settings_button.clicked.connect(lambda: SettingsDialog(self).exec())
            history_button = QPushButton("☰")
            history_button.setToolTip("打开对话记录")
            history_button.clicked.connect(self._toggle_history)
            minimize_button = QPushButton("−")
            minimize_button.setToolTip("最小化桌宠")
            minimize_button.clicked.connect(self._minimize_to_tray)
            send_button = QPushButton("➤")
            send_button.setToolTip("发送")
            send_button.clicked.connect(self._send)
            for button in (history_button, minimize_button, settings_button, send_button):
                button.setFixedSize(30, 30)
                button.setStyleSheet(
                    "QPushButton{background:transparent;color:#86868b;border:0;border-radius:15px;font-size:15px}"
                    "QPushButton:hover{background:rgba(232,134,162,35);color:#e886a2}"
                )
                input_layout.addWidget(button)
            send_button.setStyleSheet(
                "QPushButton{background:#e886a2;color:white;border:0;border-radius:15px;font-size:14px}"
                "QPushButton:hover{background:#f09bb3} QPushButton:disabled{background:#f5c6d0}"
            )
            self.send_button = send_button

            self.close_button = QPushButton("×", self)
            self.close_button.setToolTip("关闭桌宠")
            self.close_button.setGeometry(self.width() - 36, 12, 26, 26)
            self.close_button.setStyleSheet(
                "QPushButton{background:rgba(255,255,255,210);color:#aeaeb2;border:1px solid rgba(232,232,237,180);border-radius:13px;font-size:16px}"
                "QPushButton:hover{background:#e88686;color:white}"
            )
            self.close_button.clicked.connect(QApplication.quit)
            self.close_button.hide()

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

            self.timer = QTimer(self)
            self.timer.timeout.connect(self.read_frames)
            self.timer.start(16)

            self.hotkey_timer = QTimer(self)
            self.hotkey_timer.timeout.connect(self._poll_global_hotkey)
            self.hotkey_timer.start(80)

            from PySide6.QtGui import QKeySequence, QShortcut
            QShortcut(QKeySequence("Ctrl+Space"), self).activated.connect(self._focus_input)

        def _focus_input(self) -> None:
            self._restore_from_tray()
            self.input.setFocus()

        def _restore_from_tray(self) -> None:
            self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()

        def _minimize_to_tray(self) -> None:
            self.hide()

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
            self.input_panel.setVisible(not visible)
            self.reply_bubble.setVisible(not visible and bool(self.reply_bubble.text()))

        def _toggle_history(self) -> None:
            self._set_history_visible(not self._history_expanded)

        def _poll_global_hotkey(self) -> None:
            import win32api
            pressed = bool(win32api.GetAsyncKeyState(0x11) & 0x8000) and bool(win32api.GetAsyncKeyState(0x20) & 0x8000)
            if pressed and not self._hotkey_down:
                self._focus_input()
            self._hotkey_down = pressed

        def _send(self) -> None:
            text = self.input.text().strip()
            if not text or self._busy:
                return
            config = load_config()
            if not all(config.get(key) for key in ("endpoint", "api_key", "model")):
                SettingsDialog(self).exec()
                return
            self.input.clear()
            self.reply_bubble.show()
            session = active_session(self._state)
            add_message(session, "user", text)
            self.reply_bubble.setText("让我想想…")
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
            labels = {"run_command": "执行 PowerShell 命令", "open_target": "打开程序或文件",
                      "type_text": "向当前窗口输入文字", "press_keys": "按下键盘快捷键", "click": "点击屏幕"}
            details = html.escape(__import__("json").dumps(request["arguments"], ensure_ascii=False, indent=2))
            answer = QMessageBox.question(
                self, "红莉栖请求操作电脑",
                f"允许她{labels.get(request['name'], request['name'])}吗？\n\n{details}",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            request["allowed"] = answer == QMessageBox.Yes
            request["event"].set()

        def _show_status(self, text: str) -> None:
            if not self._history_expanded:
                self.reply_bubble.show()
            self.reply_bubble.setText(text if len(text) <= 34 else text[:33] + "…")

        def _agent_delta(self, text: str) -> None:
            self._streamed_reply += text
            if not self._history_expanded:
                self.reply_bubble.show()
            self.reply_bubble.setText(self._latest_line(self._streamed_reply))

        def _agent_finished(self, reply: str) -> None:
            session = active_session(self._state)
            add_message(session, "assistant", reply)
            save_state(character.id, self._state)
            self.reply_bubble.setText(self._latest_line(reply))
            self._render_history()
            self._busy = False
            self._streamed_reply = ""
            self.send_button.setDisabled(False)
            send_pet_command(emotion="smile")
            QTimer.singleShot(9000, self._hide_idle_bubble)

        def _hide_idle_bubble(self) -> None:
            if not self._busy and not self._history_expanded:
                self.reply_bubble.hide()

        def _agent_failed(self, error: str) -> None:
            self.reply_bubble.setText(self._latest_line(f"任务失败：{error}"))
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
            body = self._frame.copy(0, 0, self._frame.width(), int(self._frame.height() * 0.58))
            scaled = body.scaled(
                370, 470, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            target = QRect(
                self.width() - scaled.width() - 5,
                self.height() - scaled.height() - 78,
                scaled.width(), scaled.height(),
            )
            painter.drawImage(target, scaled)

        def mousePressEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.LeftButton:
                if event.position().x() > 390:
                    self._focus_input()
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
            self._drag_offset = None

        def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.LeftButton and event.position().x() > 440:
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
                pet.reply_bubble.setText("我已经理解你的任务。接下来会先检查当前桌面状态，\n再执行必要操作，并把最终结果告诉你。")
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
