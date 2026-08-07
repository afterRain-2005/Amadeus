"""SpritePlayer：精灵图网格动画控件。

对应原 amadeus/src/components/SpritePlayer.tsx。
原理：把一张含 rows×columns 网格的精灵图按 fps 逐帧绘制到 QWidget，
通过 QPainter 定时 update 实现 38 帧循环或定格在最后一帧。

数理背景：
- 单帧宽 frame_w = image.width / columns
- 单帧高 frame_h = image.height / rows
- 第 i 帧的源坐标：sx = (i % columns) * frame_w, sy = (i // columns) * frame_h
- 帧间隔 interval_ms = 1000 / fps
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, QRectF, QSize
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget


class SpritePlayer(QWidget):
    """精灵图动画播放控件。

    Args:
        source: 图片路径（绝对路径或 Qt 资源路径）。
        rows, columns: 网格行列数。
        fps: 帧率。
        total_frames: 总帧数（最后一行可能不满）。
        loop: 0=无限循环；1=播放一次后定格在最后一帧（与原项目 loop=1 行为一致）。
        display_size: 显示尺寸（None 表示按单帧原始尺寸）。
    """

    def __init__(
        self,
        source: str,
        rows: int,
        columns: int,
        fps: int,
        total_frames: int,
        loop: int = 0,
        display_size: Optional[QSize] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.rows = rows
        self.columns = columns
        self.fps = fps
        self.total_frames = total_frames
        self.loop = loop
        self.display_size = display_size

        self._image = QImage(source)
        if self._image.isNull():
            # 图片加载失败也保持控件存在，避免界面崩溃
            print(f"[SpritePlayer] WARNING: 无法加载图片 {source}")

        self._frame = 0
        self._frame_w = self._image.width() // columns if not self._image.isNull() else 1
        self._frame_h = self._image.height() // rows if not self._image.isNull() else 1

        # 设置控件大小
        if display_size is not None:
            self.setFixedSize(display_size)
        else:
            self.setFixedSize(self._frame_w, self._frame_h)

        # 计时器
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(int(1000 / fps))

    def _advance(self) -> None:
        if self._image.isNull():
            return
        self._frame += 1
        if self._frame >= self.total_frames:
            if self.loop == 0:
                self._frame = 0
            else:
                self._frame = self.total_frames - 1
                self._timer.stop()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if self._image.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        sx = (self._frame % self.columns) * self._frame_w
        sy = (self._frame // self.columns) * self._frame_h

        # 源矩形（图片中单帧）
        source_rect = QRectF(sx, sy, self._frame_w, self._frame_h)
        # 目标矩形（控件整体）
        target_rect = QRectF(0, 0, self.width(), self.height())
        painter.drawImage(target_rect, self._image, source_rect)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt 命名)
        if self.display_size is not None:
            return self.display_size
        return QSize(self._frame_w, self._frame_h)
