"""Shared CRT title bar used by frameless application dialogs."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)


CRT_TITLE_BAR_QSS = """
QWidget#crtTitleBar { background-color: #21171b; border: 1px solid #d2738a; border-left: 8px solid #d2738a; }
QLabel#crtTitle { color: #d2738a; font: 700 13px "Times New Roman", "Microsoft YaHei"; }
QLabel#crtSignature { color: #8a7f63; font: 10px "Consolas", "Microsoft YaHei"; }
QPushButton#crtClose { background: #171114; color: #d2738a; border: 1px solid #d2738a; min-width: 24px; max-width: 24px; min-height: 22px; max-height: 22px; padding: 0; font: 700 14px "Consolas", "Microsoft YaHei"; }
QPushButton#crtClose:hover { background: #d2738a; color: #171114; }
"""


class CrtTitleBar(QWidget):
    def __init__(
        self,
        title: str,
        signature: str,
        parent: QWidget | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("crtTitleBar")
        self.setStyleSheet(CRT_TITLE_BAR_QSS)
        self._drag_offset: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 6, 4)
        layout.setSpacing(8)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("crtTitle")
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        glow = QGraphicsDropShadowEffect(self.title_label)
        glow.setColor(QColor(210, 115, 138, 180))
        glow.setBlurRadius(14)
        glow.setOffset(1, 3)
        self.title_label.setGraphicsEffect(glow)

        self.signature_label = QLabel(signature, self)
        self.signature_label.setObjectName("crtSignature")
        self.signature_label.setAttribute(Qt.WA_TransparentForMouseEvents)

        self.close_button = QPushButton("X", self)
        self.close_button.setObjectName("crtClose")
        self.close_button.setToolTip("关闭")
        self.close_button.setAutoDefault(False)
        self.close_button.setDefault(False)
        if on_close is not None:
            self.close_button.clicked.connect(on_close)

        layout.addWidget(self.title_label)
        layout.addWidget(self.signature_label)
        layout.addStretch()
        layout.addWidget(self.close_button)

    def set_signature(self, text: str) -> None:
        self.signature_label.setText(text)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton and self._drag_offset is not None:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)
