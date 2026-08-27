"""Native Qt surface for the compact companion window."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ui.theme import BG, ROSE


ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent.parent


class CompanionSurface(QWidget):
    """Paint the terminal shell and character background entirely with Qt."""

    def __init__(self, character_rect: QRect, parent=None) -> None:
        super().__init__(parent)
        self.character_rect = QRect(character_rect)
        self.background = QPixmap(str(ROOT / "resources" / "bg.png"))
        self.texture = QPixmap(str(ROOT / "resources" / "textures" / "dither_rose.png"))
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    @staticmethod
    def _cover_source(pixmap: QPixmap, target: QRect) -> QRectF:
        scale = max(target.width() / pixmap.width(), target.height() / pixmap.height())
        source_width = target.width() / scale
        source_height = target.height() / scale
        return QRectF(
            (pixmap.width() - source_width) / 2,
            (pixmap.height() - source_height) / 2,
            source_width,
            source_height,
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(BG))
        if not self.texture.isNull():
            painter.drawTiledPixmap(self.rect(), self.texture)

        image_rect = self.character_rect.adjusted(1, 1, -1, -1)
        if not self.background.isNull():
            painter.drawPixmap(
                QRectF(image_rect),
                self.background,
                self._cover_source(self.background, image_rect),
            )

        painter.setPen(QPen(QColor(ROSE), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.character_rect.adjusted(0, 0, -1, -1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

