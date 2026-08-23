"""手机屏幕顶部状态栏（kurisu> 时间 · 在线灯 频道 信号，从 desktop_pet 提出）。"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


# ============================================================
# 类：StatusBar（QWidget）
# 作用：手机屏幕顶部状态栏——时间 + 网络信号（终端风，Qt 实现）。
#       替代原 HTML 状态栏（已从 phone_live2d_page.html 移除），
#       与 Dock 一样由 Qt 侧渲染，不经 web 截图。
# ============================================================
class StatusBar(QWidget):
    """顶部状态栏：kurisu> 时间（光标闪烁）· 在线灯 频道 信号。"""
    _MONO = "Consolas, 'Courier New', 'Microsoft YaHei', monospace"

    # ============================================================
    # 函数：__init__()
    # 作用：初始化透明背景状态栏与水平布局，创建文本控件
    # 参数：
    #   parent QWidget|None 父控件
    # 返回值：无（None）
    # ============================================================
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 3, 12, 0)
        layout.setSpacing(7)
        layout.setAlignment(Qt.AlignVCenter)
        self.setLayout(layout)

        # 左侧：kurisu> 提示符（粉）+ 时间（弱化）+ 闪烁光标（粉）
        self._prompt = QLabel("kurisu&gt;")
        self._prompt.setStyleSheet(f"color:#d2738a;font:9px {self._MONO};letter-spacing:1px;")
        self._clock = QLabel("--:--")
        self._clock.setStyleSheet(f"color:#8a7f63;font:9px {self._MONO};letter-spacing:1px;")
        self._cursor = QLabel("\u2588")
        self._cursor.setStyleSheet(f"color:#d2738a;font:9px {self._MONO};")

        # 右侧：在线灯（绿）+ 频道名（弱化）+ 信号（粉三角）
        self._dot = QLabel("\u25CF")
        self._dot.setStyleSheet("color:#34c759;font:10px Consolas,monospace;")
        self._ch = QLabel("wire ch 1")
        self._ch.setStyleSheet(f"color:#8a7f63;font:9px {self._MONO};")
        self._sig = QLabel("\u25B2\u25B2\u25B2")
        self._sig.setStyleSheet("color:#d2738a;font:9px Consolas,monospace;letter-spacing:-2px;")

        layout.addWidget(self._prompt)
        layout.addWidget(self._clock)
        layout.addWidget(self._cursor)
        layout.addStretch(1)
        layout.addWidget(self._dot)
        layout.addWidget(self._ch)
        layout.addWidget(self._sig)

        # 光标闪烁（0.5s 周期）
        self._blink = QTimer(self)
        self._blink.timeout.connect(self._toggle_cursor)
        self._blink.start(500)

        # 时钟刷新（30s 一次，同原 HTML 频率）
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._update_time)
        self._tick.start(30000)
        self._update_time()

    # ============================================================
    # 函数：_update_time()
    # 作用：刷新当前时间到时钟标签（HH:MM）
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    def _update_time(self) -> None:
        self._clock.setText(time.strftime("%H:%M"))

    # ============================================================
    # 函数：_toggle_cursor()
    # 作用：交替显示/隐藏闪烁光标块，模拟终端光标
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    def _toggle_cursor(self) -> None:
        self._cursor.setVisible(not self._cursor.isVisible())
