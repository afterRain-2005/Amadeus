"""Dock 工具栏部件：DockButton + DockBar（从 desktop_pet.run_overlay 提出）。"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QEasingCurve, QPropertyAnimation, QRectF, Qt
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ui.theme import ROSE, qcolor


ROOT = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent.parent


# ============================================================
# 类：DockButton（QPushButton）
# 作用：Dock 单个按钮：SVG 图标 + hover 放大动画 + 按压回弹。
#       fauux 刻蚀按钮物理感（hover 放大、按下缩小）。
# ============================================================
class DockButton(QPushButton):
    """Dock 单个按钮：SVG 图标 + hover 放大。"""
    BASE_SIZE = 40
    HOVER_SIZE = 48
    NEAR_SIZE = 44

    # ============================================================
    # 函数：__init__()
    # 作用：初始化按钮：设置大小/提示/鼠标指针，加载 SVG 图标
    #       （QSvgRenderer 渲染），创建 hover 放大和按下回弹动画
    # 参数：
    #   icon_name str   图标文件名（resources/icons/{name}.svg）
    #   tooltip   str   鼠标悬停提示文字
    #   is_danger bool  是否危险按钮（退出），视觉弱化
    #   parent    QWidget|None 父控件
    # 返回值：无（None）
    # ============================================================
    def __init__(self, icon_name: str, tooltip: str, is_danger: bool = False, parent=None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._is_danger = is_danger
        self._pinned = False
        self.setFixedSize(self.BASE_SIZE, self.BASE_SIZE)
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self._renderer = QSvgRenderer(QByteArray(
            (ROOT / "resources" / "icons" / f"{icon_name}.svg").read_bytes()
        ))
        self._scale = 1.0
        self._hover_anim = QPropertyAnimation(self, b"scale", self)
        self._hover_anim.setDuration(200)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)
        # 按压反馈：快速下压 + 释放回弹（fauux 刻蚀按钮物理感）
        self._press_anim = QPropertyAnimation(self, b"scale", self)
        self._press_anim.setDuration(110)
        self._press_anim.setEasingCurve(QEasingCurve.OutQuad)

    # ============================================================
    # 函数：mousePressEvent()
    # 作用：按下时播放"快速下压"动画（缩小到 88%，制造按压反馈）
    # 参数：
    #   event QMouseEvent 鼠标事件
    # 返回值：无（None）
    # ============================================================
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._hover_anim.stop()
            self._press_anim.stop()
            self._press_anim.setStartValue(self._scale)
            self._press_anim.setEndValue(max(self._scale * 0.88, 0.68))
            self._press_anim.start()
        super().mousePressEvent(event)

    # ============================================================
    # 函数：mouseReleaseEvent()
    # 作用：松开时回弹：仍在按钮上则放大到 hover 尺寸，否则恢复 1.0
    # 参数：
    #   event QMouseEvent 鼠标事件
    # 返回值：无（None）
    # ============================================================
    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._press_anim.stop()
        target = DockButton.HOVER_SIZE / DockButton.BASE_SIZE if self.underMouse() else 1.0
        self.set_target_scale(target)

    # ============================================================
    # 函数：get_scale() / set_scale()
    # 作用：缩放比例属性（property "scale" 供 QPropertyAnimation 动画用）。
    #       set_scale 会按比例改变按钮的固定尺寸并重绘。
    # 参数（set_scale）：
    #   value float 目标缩放比例（1.0=原始大小）
    # 返回值：get 返回 float 当前缩放比例；set 无返回
    # ============================================================
    def get_scale(self) -> float:
        return self._scale

    def set_scale(self, value: float) -> None:
        self._scale = value
        size = int(self.BASE_SIZE * value)
        self.setFixedSize(size, size)
        self.update()

    scale = property(get_scale, set_scale)

    # ============================================================
    # 函数：set_target_scale()
    # 作用：启动 hover 缩放动画（从当前缩放平滑过渡到目标缩放）
    # 参数：
    #   scale float 目标缩放比例
    # 返回值：无（None）
    # ============================================================
    def set_target_scale(self, scale: float) -> None:
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._scale)
        self._hover_anim.setEndValue(scale)
        self._hover_anim.start()

    # ============================================================
    # 函数：set_pinned()
    # 作用：设置固定态（v4）：固定时玫瑰常亮填充 + 实线边框，
    #       让用户能感知当前是否已固定（原 _toggle_pin 无视觉反馈）
    # 参数：
    #   pinned bool 是否固定
    # 返回值：无（None）
    # ============================================================
    def set_pinned(self, pinned: bool) -> None:
        self._pinned = pinned
        self.update()

    # ============================================================
    # 函数：paintEvent()
    # 作用：自定义绘制按钮：按状态（按下/hover/固定/危险/普通）画不同
    #       玫瑰色背景边框，再渲染 SVG 图标（按下时下沉 1px）
    # 参数：
    #   event QPaintEvent 绘制事件
    # 返回值：无（None）
    # ============================================================
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # 状态视觉（fauux：玫瑰 #d2738a，直角；按下=填充+粗框，hover=微亮）
        pressed = self.isDown()
        hovered = self.underMouse()
        if pressed:
            bg = qcolor(ROSE, 46)
            border = qcolor(ROSE, 255)
        elif hovered:
            bg = qcolor(ROSE, 10)
            border = qcolor(ROSE, 200)
        elif self._pinned:
            bg = qcolor(ROSE, 60)
            border = qcolor(ROSE, 255)
        elif self._is_danger:
            bg = QColor(0, 0, 0, 0)
            border = qcolor(ROSE, 110)
        else:
            bg = QColor(0, 0, 0, 0)
            border = qcolor(ROSE, 100)
        painter.setBrush(bg)
        if pressed:
            from PySide6.QtGui import QPen
            painter.setPen(QPen(border, 2))
        else:
            painter.setPen(border)
        painter.drawRect(self.rect())
        # SVG 图标（文件已带颜色，直接渲染；按下时下沉 1px 制造按压感）
        pad = 5 if pressed else 4
        self._renderer.render(
            painter,
            QRectF(pad, pad + (1 if pressed else 0),
                   self.width() - pad * 2, self.height() - pad * 2),
        )

# ============================================================
# 类：DockBar（QWidget）
# 作用：底部悬浮 Dock 工具栏：6 个按钮（对话/电话/固定/设置/终端/退出）
#       + hover 时邻近按钮依次放大的联动效果。
# ============================================================
class DockBar(QWidget):
    """底部悬浮 Dock 工具栏：5 按钮 + hover 邻近放大。"""
    # ============================================================
    # 函数：__init__()
    # 作用：初始化透明背景工具栏和水平布局，创建全部按钮
    # 参数：
    #   parent QWidget|None 父控件
    # 返回值：无（None）
    # ============================================================
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._buttons: list = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignCenter)
        self.setLayout(layout)
        self._build_buttons()

    # ============================================================
    # 函数：_build_buttons()
    # 作用：按规格表创建 6 个 DockButton（图标名/提示/是否危险），
    #       装入列表并加入布局，每个按钮安装事件过滤器（用于
    #       hover 联动放大）
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    def _build_buttons(self) -> None:
        specs = [
            ("chat", "对话", False),
            ("phone", "电话", False),
            ("pin", "固定", False),
            ("settings", "设置", False),
            ("terminal", "终端", False),
            ("close", "退出", True),
        ]
        for icon_name, tooltip, is_danger in specs:
            btn = DockButton(icon_name, tooltip, is_danger, self)
            btn.installEventFilter(self)
            self._buttons.append(btn)
            self.layout().addWidget(btn)

    # ============================================================
    # 函数：eventFilter()
    # 作用：监听按钮的鼠标 Enter/Leave 事件，触发邻近放大动画
    # 参数：
    #   obj   QObject 事件来源对象
    #   event QEvent   事件
    # 返回值：bool —— True=事件已处理；False=继续传给父类
    # ============================================================
    def eventFilter(self, obj, event) -> bool:
        if obj in self._buttons:
            idx = self._buttons.index(obj)
            if event.type() == event.Type.Enter:
                self._apply_hover_scale(idx)
            elif event.type() == event.Type.Leave:
                self._apply_leave_scale()
        return super().eventFilter(obj, event)

    # ============================================================
    # 函数：_apply_hover_scale()
    # 作用：hover 联动：被 hover 的按钮放大到 HOVER_SIZE，
    #       相邻按钮放大到 NEAR_SIZE，其余恢复 1.0
    # 参数：
    #   hover_idx int 被 hover 按钮的索引
    # 返回值：无（None）
    # ============================================================
    def _apply_hover_scale(self, hover_idx: int) -> None:
        for i, btn in enumerate(self._buttons):
            dist = abs(i - hover_idx)
            if dist == 0:
                btn.set_target_scale(DockButton.HOVER_SIZE / DockButton.BASE_SIZE)
            elif dist == 1:
                btn.set_target_scale(DockButton.NEAR_SIZE / DockButton.BASE_SIZE)
            else:
                btn.set_target_scale(1.0)

    # ============================================================
    # 函数：_apply_leave_scale()
    # 作用：鼠标离开 Dock 时所有按钮恢复原始大小
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    def _apply_leave_scale(self) -> None:
        for btn in self._buttons:
            btn.set_target_scale(1.0)

    # ============================================================
    # 函数：button()
    # 作用：按提示文字查找按钮（如 button("对话")）
    # 参数：
    #   name str 按钮 tooltip（提示文字）
    # 返回值：DockButton —— 匹配的按钮；找不到抛 KeyError
    # ============================================================
    def button(self, name: str):
        for btn in self._buttons:
            if btn.toolTip() == name:
                return btn
        raise KeyError(name)
