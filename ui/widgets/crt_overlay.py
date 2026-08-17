"""Reusable CRT effect overlay (scanlines / vignette / animated noise).

A transparent, mouse-transparent child widget drawn on top of its parent,
mimicking the Wired Sound CRT look without changing the parent's colors.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import QWidget


class CrtOverlay(QWidget):
    """透明特效层：扫描线 + 暗角 + 可选静电噪点。

    作为父控件的子控件覆盖其上；WA_TransparentForMouseEvents 保证点击穿透，
    随父控件 resize 自动重设几何（事件过滤器监听父控件 Resize 事件）。
    """

    def __init__(self, parent, scanlines: bool = True, vignette: bool = True, noise: bool = False):
        super().__init__(parent)
        self._scanlines = scanlines
        self._vignette = vignette
        self._noise = noise
        self._noise_seed = 0
        self._noise_timer: QTimer | None = None

        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setGeometry(parent.rect())
        parent.installEventFilter(self)

        if noise:
            self._noise_timer = QTimer(self)
            self._noise_timer.setInterval(80)
            self._noise_timer.timeout.connect(self._advance_noise)
            self._noise_timer.start()

    def _advance_noise(self) -> None:
        self._noise_seed = (self._noise_seed + 1) % 10000
        self.update()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.parent() and event.type() == QEvent.Resize:
            self.setGeometry(self.parent().rect())
        return super().eventFilter(obj, event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            if self._vignette:
                self._paint_vignette(painter)
            if self._scanlines:
                self._paint_scanlines(painter)
            if self._noise:
                self._paint_noise(painter)
        finally:
            painter.end()

    def _paint_vignette(self, painter: QPainter) -> None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        gradient = QRadialGradient(w / 2, h / 2, max(w, h) * 0.75)
        gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        gradient.setColorAt(0.55, QColor(0, 0, 0, 0))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 130))
        painter.fillRect(self.rect(), gradient)

    def _paint_scanlines(self, painter: QPainter) -> None:
        painter.setPen(QColor(0, 0, 0, 45))
        for y in range(0, self.height(), 3):
            painter.drawLine(0, y, self.width(), y)

    def _paint_noise(self, painter: QPainter) -> None:
        import random

        rnd = random.Random(self._noise_seed)
        painter.setPen(QColor(193, 180, 146, 20))
        count = max(0, self.width() * self.height() // 120)
        for _ in range(count):
            painter.drawPoint(rnd.randrange(self.width()), rnd.randrange(self.height()))
