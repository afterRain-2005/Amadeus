"""Feature-complete compact chat window for Amadeus."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QKeySequence, QMouseEvent, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPushButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from config import get_character_by_id, get_random_greeting
from core.emotion_parser import parse_reply
from core.asr_client import encode_wav, transcribe
from core.image_utils import image_data_url
from core.llm_client import stream_chat
from core.pet_controller import send_pet_command
from core.session_manager import (
    active_session, add_message, create_session, export_session, load_state, save_state,
)
from core.storage import load_config
from core.tts_client import SpeechPlayer
from ui.settings_dialog import SettingsDialog


class WorkerSignals(QObject):
    delta = Signal(str)
    finished = Signal(str)
    failed = Signal(str)


class ChatTask(QRunnable):
    def __init__(self, character, history, memories, config) -> None:
        super().__init__()
        self.character, self.history, self.memories, self.config = character, history, memories, config
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            reply = stream_chat(
                endpoint=self.config["endpoint"], api_key=self.config["api_key"], model=self.config["model"],
                personality=self.character.personality, history=self.history, memories=self.memories,
                on_delta=self.signals.delta.emit,
            )
            self.signals.finished.emit(reply)
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class AsrTask(QRunnable):
    def __init__(self, samples, sample_rate, config) -> None:
        super().__init__()
        self.samples, self.sample_rate, self.config = samples, sample_rate, config
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            key = self.config.get("asr_api_key") or self.config.get("api_key", "")
            text = transcribe(
                encode_wav(self.samples, self.sample_rate), self.config.get("asr_endpoint", ""),
                key, self.config.get("asr_model", "mimo-v2.5-asr"),
            )
            self.signals.finished.emit(text)
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class ChatWindow(QWidget):
    logout_requested = Signal()

    def __init__(self, character_id: str, parent=None) -> None:
        super().__init__(parent)
        self.character = get_character_by_id(character_id)
        self._drag_pos: QPoint | None = None
        self._pending_reply = ""
        self._image_path = ""
        self._busy = False
        self._recording = False
        self._audio_chunks = []
        self._audio_stream = None
        self._state = load_state(character_id, get_random_greeting(character_id))
        self.speech = SpeechPlayer(self)
        self.speech.speaking_changed.connect(lambda value: send_pet_command(speaking=value))
        self.setWindowTitle(f"Amadeus · {self.character.name}")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(720, 560)
        self._build_ui()
        QShortcut(QKeySequence.Paste, self).activated.connect(self._paste_image)
        self._reload_sessions()
        self._render_messages()

    def _build_ui(self) -> None:
        self.setStyleSheet("QWidget{font-family:'Microsoft YaHei';color:#eee} QToolTip{color:#111;background:#eee}")
        title = QLabel(f"AMADEUS  /  {self.character.name}")
        title.setStyleSheet("color:#efaa35;font-weight:700;padding:8px")
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        title_row.addStretch()
        for text, tip, callback in [
            ("＋", "新建会话", self._new_session), ("⇩", "导出当前会话", self._export),
            ("⚙", "设置", self._settings), ("−", "隐藏", self.hide), ("×", "退出登录", self.logout_requested.emit),
        ]:
            title_row.addWidget(self._icon_button(text, tip, callback))
        title_widget = QWidget()
        title_widget.setLayout(title_row)
        title_widget.setStyleSheet("background:#11151d;border-radius:8px 8px 0 0")

        self.sessions = QListWidget()
        self.sessions.setFixedWidth(155)
        self.sessions.currentRowChanged.connect(self._switch_session)
        self.sessions.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sessions.customContextMenuRequested.connect(self._session_menu)
        self.sessions.setStyleSheet("QListWidget{background:#0b0e14;border:0;padding:5px} QListWidget::item{padding:9px;border-radius:4px} QListWidget::item:selected{background:#3a2a17;color:#efaa35}")

        self.messages = QTextBrowser()
        self.messages.setOpenExternalLinks(False)
        self.messages.setStyleSheet("QTextBrowser{background:#070a0f;border:0;padding:12px}")
        splitter = QSplitter()
        splitter.addWidget(self.sessions)
        splitter.addWidget(self.messages)
        splitter.setStretchFactor(1, 1)

        self.preview = QLabel()
        self.preview.setFixedHeight(0)
        self.preview.setStyleSheet("background:#10141c;padding:4px")
        self.input = QLineEdit()
        self.input.setPlaceholderText("和红莉栖说点什么…")
        self.input.returnPressed.connect(self._send)
        self.input.setStyleSheet("QLineEdit{background:#11151e;border:1px solid #705126;border-radius:4px;padding:8px}")
        image_button = self._icon_button("▧", "发送图片", self._choose_image)
        self.mic_button = self._icon_button("●", "开始/停止语音输入", self._toggle_recording)
        self.send_button = QPushButton("发送")
        self.send_button.clicked.connect(self._send)
        self.send_button.setStyleSheet("QPushButton{background:#c47d20;border:0;border-radius:4px;padding:9px 18px} QPushButton:disabled{background:#514638}")
        input_row = QHBoxLayout()
        input_row.setContentsMargins(8, 7, 8, 8)
        input_row.addWidget(image_button)
        input_row.addWidget(self.mic_button)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_button)
        input_widget = QWidget()
        input_widget.setLayout(input_row)
        input_widget.setStyleSheet("background:#11151d;border-radius:0 0 8px 8px")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(title_widget)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.preview)
        layout.addWidget(input_widget)

    def _icon_button(self, text, tooltip, callback):
        button = QPushButton(text)
        button.setToolTip(tooltip)
        button.setFixedSize(30, 30)
        button.clicked.connect(callback)
        button.setStyleSheet("QPushButton{background:transparent;border:0;color:#aaa;font-size:18px} QPushButton:hover{color:#efaa35;background:#292117;border-radius:4px}")
        return button

    @property
    def session(self):
        return active_session(self._state)

    def _reload_sessions(self) -> None:
        self.sessions.blockSignals(True)
        self.sessions.clear()
        selected = 0
        for index, session in enumerate(self._state["sessions"]):
            item = QListWidgetItem(f"{session['name']}\n{len(session['messages'])} 条")
            item.setData(Qt.UserRole, session["id"])
            self.sessions.addItem(item)
            if session["id"] == self._state["active_id"]:
                selected = index
        self.sessions.setCurrentRow(selected)
        self.sessions.blockSignals(False)

    def _render_messages(self) -> None:
        blocks = []
        for message in self.session["messages"]:
            assistant = message["role"] == "assistant"
            parsed = parse_reply(message["content"]) if assistant else None
            text = parsed.chinese if parsed else message["content"]
            name = self.character.name if assistant else "你"
            color, background = ("#efaa35", "#2b2117") if assistant else ("#92baff", "#17243a")
            image = ""
            if message.get("image_path") and Path(message["image_path"]).exists():
                image = f"<br><img src='{Path(message['image_path']).as_uri()}' width='180'>"
            blocks.append(f"<div style='background:{background};padding:10px;margin:7px;border-radius:6px'><b style='color:{color}'>{name}</b><br>{html.escape(text).replace(chr(10), '<br>')}{image}</div>")
        self.messages.setHtml("".join(blocks))
        self.messages.verticalScrollBar().setValue(self.messages.verticalScrollBar().maximum())

    def _send(self) -> None:
        text = self.input.text().strip()
        if self._busy or (not text and not self._image_path):
            return
        display_text = text or "请看看这张图片。"
        image_path = self._image_path
        add_message(self.session, "user", display_text, image_path)
        self.input.clear()
        self._clear_image()
        config = load_config()
        if not all(config.get(key) for key in ("endpoint", "api_key", "model")):
            QMessageBox.information(self, "需要模型配置", "请先在设置中填写 OpenAI 兼容 API、Key 和模型。")
            self._settings()
            self._render_messages()
            save_state(self.character.id, self._state)
            return
        history = []
        for message in self.session["messages"][-12:]:
            content = message["content"]
            if message.get("image_path"):
                content = [{"type": "text", "text": content}, {"type": "image_url", "image_url": {"url": image_data_url(message["image_path"])}}]
            history.append({"role": message["role"], "content": content})
        self._busy = True
        self.send_button.setDisabled(True)
        self._pending_reply = ""
        add_message(self.session, "assistant", "思考中…")
        self._render_messages()
        task = ChatTask(self.character, history, self.session.get("memories", []), config)
        task.signals.delta.connect(self._delta)
        task.signals.finished.connect(self._finished)
        task.signals.failed.connect(self._failed)
        QThreadPool.globalInstance().start(task)

    @Slot(str)
    def _delta(self, text: str) -> None:
        self._pending_reply += text
        self.session["messages"][-1]["content"] = self._pending_reply
        self._render_messages()

    @Slot(str)
    def _finished(self, reply: str) -> None:
        self.session["messages"][-1]["content"] = reply or self._pending_reply
        self._busy = False
        self.send_button.setDisabled(False)
        parsed = parse_reply(self.session["messages"][-1]["content"])
        send_pet_command(emotion=parsed.emotion)
        config = load_config()
        if config.get("tts_enabled", True) and parsed.japanese:
            self.speech.set_rate([-2, 0, 2][config.get("tts_rate", 1)])
            self.speech.speak(parsed.japanese)
        save_state(self.character.id, self._state)
        self._reload_sessions()
        self._render_messages()

    @Slot(str)
    def _failed(self, error: str) -> None:
        self.session["messages"][-1]["content"] = f"[emotion:angry]（皱眉）连接模型失败：{error}\n===\n（眉をひそめる）接続に失敗したわ。"
        self._busy = False
        self.send_button.setDisabled(False)
        send_pet_command(emotion="angry")
        save_state(self.character.id, self._state)
        self._render_messages()

    def _choose_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return
        self._set_image(path)

    def _set_image(self, path: str) -> None:
        self._image_path = path
        pixmap = QPixmap(path).scaled(90, 70, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview.setPixmap(pixmap)
        self.preview.setFixedHeight(78)
        self.preview.setToolTip("点击图片按钮可重新选择")

    def _paste_image(self) -> None:
        from PySide6.QtWidgets import QApplication
        image = QApplication.clipboard().image()
        if image.isNull():
            self.input.paste()
            return
        image_dir = Path(__file__).resolve().parent.parent / "data" / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        path = image_dir / f"clipboard-{__import__('time').time_ns()}.png"
        if image.save(str(path), "PNG"):
            self._set_image(str(path))

    def _toggle_recording(self) -> None:
        if not self._recording:
            try:
                import sounddevice as sd
                self._audio_chunks = []
                self._audio_stream = sd.InputStream(
                    samplerate=16000, channels=1, dtype="float32",
                    callback=lambda data, frames, time_info, status: self._audio_chunks.append(data.copy()),
                )
                self._audio_stream.start()
                self._recording = True
                self.mic_button.setText("■")
                self.mic_button.setToolTip("停止录音并识别")
            except Exception as exc:
                QMessageBox.warning(self, "无法录音", str(exc))
            return

        self._recording = False
        self.mic_button.setText("●")
        self.mic_button.setToolTip("开始语音输入")
        if self._audio_stream is not None:
            self._audio_stream.stop()
            self._audio_stream.close()
            self._audio_stream = None
        if not self._audio_chunks:
            return
        import numpy as np
        samples = np.concatenate(self._audio_chunks, axis=0).reshape(-1)
        config = load_config()
        if not (config.get("asr_api_key") or config.get("api_key")):
            QMessageBox.information(self, "需要 ASR 配置", "请在设置中填写 ASR API Key，或配置对话 API Key 作为回退。")
            return
        self.mic_button.setDisabled(True)
        task = AsrTask(samples, 16000, config)
        task.signals.finished.connect(self._asr_finished)
        task.signals.failed.connect(self._asr_failed)
        QThreadPool.globalInstance().start(task)

    def _asr_finished(self, text: str) -> None:
        self.mic_button.setDisabled(False)
        self.input.setText(text)
        self.input.setFocus()

    def _asr_failed(self, error: str) -> None:
        self.mic_button.setDisabled(False)
        QMessageBox.warning(self, "语音识别失败", error)

    def _clear_image(self) -> None:
        self._image_path = ""
        self.preview.clear()
        self.preview.setFixedHeight(0)

    def _new_session(self) -> None:
        create_session(self._state, get_random_greeting(self.character.id))
        save_state(self.character.id, self._state)
        self._reload_sessions()
        self._render_messages()

    def _switch_session(self, row: int) -> None:
        if row < 0:
            return
        self._state["active_id"] = self.sessions.item(row).data(Qt.UserRole)
        save_state(self.character.id, self._state)
        self._render_messages()

    def _session_menu(self, pos) -> None:
        item = self.sessions.itemAt(pos)
        if item is None or len(self._state["sessions"]) <= 1:
            return
        menu = QMenu(self)
        delete_action = menu.addAction("删除会话")
        if menu.exec(self.sessions.mapToGlobal(pos)) is delete_action:
            session_id = item.data(Qt.UserRole)
            self._state["sessions"] = [s for s in self._state["sessions"] if s["id"] != session_id]
            self._state["active_id"] = self._state["sessions"][0]["id"]
            save_state(self.character.id, self._state)
            self._reload_sessions()
            self._render_messages()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出会话", f"{self.session['name']}.txt", "文本 (*.txt)")
        if path:
            export_session(self.session, self.character.name, Path(path))

    def _settings(self) -> None:
        SettingsDialog(self).exec()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and event.position().y() <= 45:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_pos = None
