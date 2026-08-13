# ui/widgets/call_view.py
"""通话态视图：三区布局（顶部状态条 / 中部字幕+波形+屏幕缩略图 / 底部三按钮）。

配色沿用 A2 青蓝（#00d4ff 强调 + 半透青气泡），SVG 矢量按钮。
移植原项目 VoiceCall.tsx 的波形 canvas + 状态文案，适配 PySide6。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

_ROOT = Path(__file__).resolve().parent.parent.parent
_ICONS = _ROOT / "resources" / "icons"


class WaveformCanvas(QWidget):
    """简易波形条：set_waveform(level) 触发重绘。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(120, 24)
        self._level = 0.0
        self._bars = 16

    def set_waveform(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        gap = 2
        bar_w = (w - gap * (self._bars - 1)) / self._bars
        for i in range(self._bars):
            # 中间高两边低，叠加实时 level
            center_factor = 1.0 - abs(i - self._bars / 2) / (self._bars / 2)
            v = self._level * center_factor + 0.08
            bar_h = max(2, v * h)
            x = i * (bar_w + gap)
            y = (h - bar_h) / 2
            p.fillRect(QRectF(x, y, bar_w, bar_h), QColor(0, 212, 255, 200))


class _SvgButton(QPushButton):
    """SVG 矢量圆形按钮。SVG 文件不存在时降级为纯色圆形。"""

    def __init__(self, icon_name: str, size: int = 44, color: str = "cyan", parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        icon_path = _ICONS / f"{icon_name}.svg"
        if icon_path.exists():
            self._renderer = QSvgRenderer(icon_path.read_bytes())
        else:
            self._renderer = None
        self._color = color

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._color == "red":
            bg = QColor(255, 59, 48, 200)
            border = QColor(255, 59, 48, 255)
        elif self._color == "amber":
            bg = QColor(255, 176, 58, 180)
            border = QColor(255, 176, 58, 255)
        else:
            bg = QColor(0, 212, 255, 40)
            border = QColor(0, 212, 255, 120)
        p.setBrush(bg)
        p.setPen(border)
        p.drawEllipse(self.rect())
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        # 顶部：状态条（红点 + 状态文案 + 时长）
        top = QHBoxLayout()
        top.setSpacing(6)
        self._dot = QLabel("●", self)
        self._dot.setStyleSheet("color:#ff3b30; font-size:10px")
        self.status_label = QLabel("正在接通…", self)
        self.status_label.setStyleSheet(
            "color:#7be8ff; font:12px 'Segoe UI','Microsoft YaHei';"
            "background:rgba(0,212,255,0.12); border:1px solid rgba(0,212,255,0.3);"
            "border-radius:10px; padding:3px 10px"
        )
        self.elapsed_label = QLabel("0:00", self)
        self.elapsed_label.setStyleSheet("color:#8e8e93; font:11px 'Consolas'")
        top.addWidget(self._dot)
        top.addWidget(self.status_label)
        top.addStretch()
        top.addWidget(self.elapsed_label)
        layout.addLayout(top)

        # 中部：字幕 + 波形
        self.subtitle_label = QLabel("正在接通…", self)
        self.subtitle_label.setAlignment(Qt.AlignCenter)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setStyleSheet(
            "color:#7be8ff; font:14px 'Segoe UI','Microsoft YaHei';"
            "background:rgba(0,212,255,0.10); border:1px solid rgba(0,212,255,0.3);"
            "border-radius:14px; padding:10px 16px"
        )
        layout.addWidget(self.subtitle_label)

        self.waveform = WaveformCanvas(self)
        layout.addWidget(self.waveform, alignment=Qt.AlignCenter)

        layout.addStretch()

        # 底部：三按钮
        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        bottom.setAlignment(Qt.AlignCenter)
        self.mute_btn = _SvgButton("mic", 44, "cyan")
        self.mute_btn.setToolTip("静音")
        self.mute_btn.clicked.connect(self.mute_clicked.emit)
        self.hangup_btn = _SvgButton("hangup", 52, "red")
        self.hangup_btn.setToolTip("挂断")
        self.hangup_btn.clicked.connect(self.hangup_clicked.emit)
        self.screen_btn = _SvgButton("screen_share", 44, "cyan")
        self.screen_btn.setToolTip("屏幕共享")
        self.screen_btn.clicked.connect(self.screen_clicked.emit)
        bottom.addWidget(self.mute_btn)
        bottom.addWidget(self.hangup_btn)
        bottom.addWidget(self.screen_btn)
        layout.addLayout(bottom)

    # ===== 外部驱动接口 =====
    def set_phase(self, phase: str) -> None:
        status_map = {
            "connecting": "正在接通…",
            "listening": "通话中 · 聆听中",
            "processing": "通话中 · 处理中",
            "speaking": "通话中",
            "ended": "通话结束",
            "idle": "",
        }
        self.status_label.setText(status_map.get(phase, phase))
        dot_color = "#ffb63a" if phase == "connecting" else "#34c759" if phase in ("listening", "speaking", "processing") else "#8e8e93"
        self._dot.setStyleSheet(f"color:{dot_color}; font-size:10px")

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)

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
        self.mute_btn.update()

    def set_screen_share(self, on: bool) -> None:
        self.screen_btn.setToolTip("屏幕共享：开" if on else "屏幕共享：关")
        self.screen_btn.update()