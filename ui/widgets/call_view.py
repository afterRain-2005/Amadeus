# ui/widgets/call_view.py
"""通话态视图：与 Agent Terminal 共用视觉语言的语音通道面板。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ui.theme import BG, CREAM, DIM, PANEL, ROSE, qcolor

_ROOT = Path(__file__).resolve().parent.parent.parent
_ICONS = _ROOT / "resources" / "icons"
_MONO = "Consolas"


class WaveformCanvas(QWidget):
    """简易波形条：set_waveform(level) 触发重绘。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(144, 30)
        self._level = 0.0
        self._bars = 16

    def set_waveform(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        gap = 2
        bar_w = (w - gap * (self._bars - 1)) / self._bars
        p.setPen(QPen(qcolor(DIM, 90), 1))
        p.drawLine(0, h // 2, w, h // 2)
        for i in range(self._bars):
            # 中间高两边低，叠加实时 level
            center_factor = 1.0 - abs(i - self._bars / 2) / (self._bars / 2)
            v = self._level * center_factor + 0.08
            bar_h = max(2, v * h)
            x = i * (bar_w + gap)
            y = (h - bar_h) / 2
            alpha = 120 + int(self._level * 100)
            p.fillRect(QRectF(x, y, bar_w, bar_h), qcolor(ROSE, alpha))


class _SvgButton(QPushButton):
    """终端方键式 SVG 按钮；SVG 缺失时保留边框作为降级显示。"""

    def __init__(self, icon_name: str, size: int = 44, color: str = "normal", parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(icon_name)
        icon_path = _ICONS / f"{icon_name}.svg"
        if icon_path.exists():
            icon_data = icon_path.read_bytes()
            if color == "red":
                icon_data = icon_data.replace(ROSE.encode(), BG.encode())
            self._renderer = QSvgRenderer(icon_data)
        else:
            self._renderer = None
        self._color = color

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        # 所有按钮都使用终端方键；危险操作使用更强的玫红底色。
        if self._color == "red":
            bg = qcolor(ROSE, 46)
            border = qcolor(ROSE, 255)
        elif self._color == "amber":
            bg = qcolor(CREAM, 36)
            border = qcolor(CREAM, 210)
        elif self._color == "active":
            bg = qcolor(ROSE, 46)
            border = qcolor(ROSE, 255)
        else:
            bg = qcolor(BG, 220)
            border = qcolor(ROSE, 150)
        p.setBrush(bg)
        p.setPen(border)
        p.drawRect(self.rect())
        if self._renderer is not None:
            pad = 10
            self._renderer.render(p, QRectF(pad, pad, self.width() - pad * 2, self.height() - pad * 2))


class CallView(QWidget):
    """通话态三区布局视图。由 VoiceCallController 信号驱动。"""

    mute_clicked = Signal()
    hangup_clicked = Signal()
    screen_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(7)

        # 顶部：与 Agent Terminal 标题条一致的通道状态。
        top = QHBoxLayout()
        top.setSpacing(0)
        self._dot = QLabel("■", self)
        self._dot.setFixedWidth(12)
        self._dot.setAlignment(Qt.AlignCenter)
        self._dot.setStyleSheet(f"color:{CREAM};font-family:{_MONO};font-size:8px;background:{PANEL};border:1px solid {ROSE};border-right:0;")
        self.status_label = QLabel("CALL/CONNECTING · 正在接通", self)
        self.status_label.setStyleSheet(
            f"color:{CREAM};font-family:{_MONO};font-size:10px;font-weight:700;"
            f"background:{PANEL};border:1px solid {ROSE};border-left:5px solid {ROSE};"
            "border-radius:0;padding:5px 7px;"
        )
        self.elapsed_label = QLabel("0:00", self)
        self.elapsed_label.setAlignment(Qt.AlignCenter)
        self.elapsed_label.setStyleSheet(
            f"color:{DIM};font-family:{_MONO};font-size:10px;background:{BG};"
            f"border:1px solid {DIM};border-left:0;padding:5px 6px;"
        )
        top.addWidget(self._dot)
        top.addWidget(self.status_label, 1)
        top.addWidget(self.elapsed_label)
        layout.addLayout(top)

        self.input_meta_label = QLabel("VOICE.INPUT / STANDBY", self)
        self.input_meta_label.setStyleSheet(
            f"color:{DIM};font-family:{_MONO};font-size:8px;background:transparent;padding:0 4px;"
        )
        layout.addWidget(self.input_meta_label)

        # 中部：语音识别结果 + 波形，保留用户调整后的靠上布局。
        self.you_said_label = QLabel("you> waiting for input_", self)
        self.you_said_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.you_said_label.setWordWrap(True)
        self.you_said_label.setStyleSheet(
            f"color:{DIM};font-family:{_MONO};font-size:10px;background:rgba(23,17,20,180);"
            f"border-left:3px solid {DIM};padding:5px 7px;"
        )
        layout.addWidget(self.you_said_label)

        self.waveform = WaveformCanvas(self)
        layout.addWidget(self.waveform, alignment=Qt.AlignCenter)

        layout.addStretch()

        self.output_meta_label = QLabel("KURISU.OUTPUT / CARRIER", self)
        self.output_meta_label.setStyleSheet(
            f"color:{ROSE};font-family:{_MONO};font-size:8px;background:transparent;padding:0 4px;"
        )
        layout.addWidget(self.output_meta_label)

        # 底部：回答框继续与正常聊天气泡同位，并改用同款边框层级。
        self.subtitle_label = QLabel("kurisu> 正在接通…", self)
        self.subtitle_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setStyleSheet(
            f"color:{CREAM};font-family:{_MONO};font-size:12px;background:{BG};"
            f"border:1px solid {ROSE};border-left:6px solid {ROSE};"
            "border-radius:0;padding:10px 12px;"
        )
        layout.addWidget(self.subtitle_label)

        # 底部：终端方键 + 命令标签。
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        bottom.setAlignment(Qt.AlignCenter)
        self.mute_btn = _SvgButton("mic", 42, "normal")
        self.mute_btn.setToolTip("静音")
        self.mute_btn.clicked.connect(self.mute_clicked.emit)
        self.hangup_btn = _SvgButton("hangup", 46, "red")
        self.hangup_btn.setToolTip("挂断")
        self.hangup_btn.clicked.connect(self.hangup_clicked.emit)
        self.screen_btn = _SvgButton("screen_share", 42, "normal")
        self.screen_btn.setToolTip("屏幕共享")
        self.screen_btn.clicked.connect(self.screen_clicked.emit)
        for button, command in (
            (self.mute_btn, "[MUTE]"),
            (self.hangup_btn, "[END]"),
            (self.screen_btn, "[SHARE]"),
        ):
            group = QVBoxLayout()
            group.setSpacing(2)
            group.setAlignment(Qt.AlignCenter)
            caption = QLabel(command, self)
            caption.setAlignment(Qt.AlignCenter)
            caption.setStyleSheet(f"color:{DIM};font-family:{_MONO};font-size:8px;background:transparent;")
            group.addWidget(button, alignment=Qt.AlignCenter)
            group.addWidget(caption)
            bottom.addLayout(group)
        layout.addLayout(bottom)

    # ===== 外部驱动接口 =====
    def set_phase(self, phase: str) -> None:
        status_map = {
            "connecting": "CALL/CONNECTING · 正在接通",
            "listening": "CALL/LISTENING · 聆听中",
            "processing": "CALL/PROCESSING · 处理中",
            "speaking": "CALL/TRANSMITTING · 通话中",
            "ended": "CALL/ENDED · 通话结束",
            "idle": "",
        }
        self.status_label.setText(status_map.get(phase, phase))
        dot_color = CREAM if phase == "connecting" else ROSE if phase in ("listening", "speaking", "processing") else DIM
        self._dot.setStyleSheet(
            f"color:{dot_color};font-family:{_MONO};font-size:8px;background:{PANEL};"
            f"border:1px solid {ROSE};border-right:0;"
        )

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(f"kurisu> {text}")

    def set_you_said(self, text: str) -> None:
        """显示最近一次语音识别结果（常驻小字，直到下一次识别覆盖）。"""
        self.you_said_label.setText(f"you> {text}")

    def set_elapsed(self, seconds: int) -> None:
        m = seconds // 60
        s = seconds % 60
        self.elapsed_label.setText(f"{m}:{s:02d}")

    def set_waveform(self, level: float) -> None:
        self.waveform.set_waveform(level)

    def set_muted(self, muted: bool) -> None:
        icon_path = _ICONS / ("mic_off.svg" if muted else "mic.svg")
        if icon_path.exists():
            self.mute_btn._renderer = QSvgRenderer(icon_path.read_bytes())
        self.mute_btn._color = "amber" if muted else "normal"
        self.mute_btn.setToolTip("取消静音" if muted else "静音")
        self.mute_btn.update()

    def set_screen_share(self, on: bool) -> None:
        self.screen_btn.setToolTip("屏幕共享：开" if on else "屏幕共享：关")
        self.screen_btn._color = "active" if on else "normal"
        self.screen_btn.update()
