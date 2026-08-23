"""CRT glitch 双色分裂标题标签（从 desktop_pet 提出；当前无调用点，保留备用）。"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget


# ============================================================
# 类：GlitchLabel（QWidget）
# 作用：全角标题 + CRT glitch 双色分裂动画（参考 fauux .glitch）。
#       主文字 cream，两层偏移副本（rose 浅 / dim 暗）只在偶发 glitch
#       帧的随机水平条带内显示，制造 RGB 分裂撕裂感；多数时间保持稳定。
# ============================================================
class GlitchLabel(QWidget):
    """全角标题 + CRT glitch 双色分裂动画（参考 fauux .glitch）。

    主文字 cream，两层偏移副本（rose 浅 / dim 暗）只在偶发 glitch 帧的
    随机水平条带内显示，制造 RGB 分裂撕裂感；多数时间保持稳定不跳动。
    配色保持 rose/cream 系，色调不变。
    """

    # ============================================================
    # 函数：__init__()
    # 作用：初始化标签文字，启动 70ms 定时器驱动 glitch 动画帧
    # 参数：
    #   text   str          显示的标题文字
    #   parent QWidget|None 父控件
    # 返回值：无（None）
    # ============================================================
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._text = text
        self._seed = 0
        self._glitch_frames = 0
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._timer = QTimer(self)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self._advance)
        self._timer.start()

    # ============================================================
    # 函数：_advance()
    # 作用：动画推进：更新随机种子，约 3% 概率进入 glitch 状态
    #       （持续 2~4 帧约 140~280ms），其余时间保持稳定，然后重绘
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    def _advance(self) -> None:
        self._seed = (self._seed + 1) % 10000
        import random
        rnd = random.Random(self._seed)
        if self._glitch_frames > 0:
            self._glitch_frames -= 1
        elif rnd.random() < 0.03:
            # 偶发撕裂：持续 2~4 帧（约 140~280ms），其余时间保持稳定
            self._glitch_frames = rnd.randint(2, 4)
        self.update()

    # ============================================================
    # 函数：_font()
    # 作用：构建标题字体（Consolas 15px 粗体 + 112% 字距）
    # 参数：无
    # 返回值：QFont —— 标题字体
    # ============================================================
    def _font(self) -> QFont:
        font = QFont("Times New Roman")
        font.setPixelSize(13)
        font.setBold(True)
        return font

    # ============================================================
    # 函数：sizeHint()
    # 作用：根据文字宽度估算控件的建议尺寸
    # 参数：无
    # 返回值：QSize —— 建议尺寸（文字宽 + 16，文字高 + 10）
    # ============================================================
    def sizeHint(self):
        fm = QFontMetrics(self._font())
        return QSize(fm.horizontalAdvance(self._text) + 16, fm.height() + 10)

    # ============================================================
    # 函数：paintEvent()
    # 作用：绘制标题：先画主文字（cream），glitch 状态时在随机
    #       水平条带内画 rose/dim 两层偏移副本（模拟 RGB 撕裂）
    # 参数：
    #   event QPaintEvent 绘制事件
    # 返回值：无（None）
    # ============================================================
    def paintEvent(self, event) -> None:
        import random
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setFont(self._font())
            rect = self.rect()
            # 主文字 cream
            painter.setPen(QColor(193, 180, 146))
            painter.drawText(rect, Qt.AlignCenter, self._text)

            if self._glitch_frames <= 0:
                return
            rnd = random.Random(self._seed)
            # 随机 1~3 个水平条带，仅在条带内显示偏移副本（模拟 clip-path 撕裂）
            for _ in range(rnd.randint(1, 3)):
                y = rnd.randrange(max(1, self.height()))
                h = rnd.randint(2, 7)
                painter.save()
                painter.setClipRect(0, y, self.width(), h)
                painter.setPen(QColor(210, 115, 138))
                painter.drawText(rect.translated(-2, -1), Qt.AlignCenter, self._text)
                painter.restore()
                painter.save()
                painter.setClipRect(0, y, self.width(), h)
                painter.setPen(QColor(138, 127, 99))
                painter.drawText(rect.translated(2, 1), Qt.AlignCenter, self._text)
                painter.restore()
        finally:
            painter.end()
