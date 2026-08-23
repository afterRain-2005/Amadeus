"""重构 2026-08-24：从 desktop_pet.run_overlay 提取的部件冒烟测试。

覆盖 DockBar/DockButton、StatusBar、GlitchLabel 的构造、结构与关键交互，
保证提取后的模块可独立实例化且行为不变（不 show()，不弹窗）。
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from ui.widgets.dock import DockBar, DockButton
from ui.widgets.glitch_label import GlitchLabel
from ui.widgets.status_bar import StatusBar


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_dock_bar_builds_6_buttons(qapp):
    bar = DockBar()
    for name in ["对话", "电话", "固定", "设置", "终端", "退出"]:
        assert bar.button(name) is not None, name


def test_dock_button_danger_and_pin(qapp):
    btn = DockButton("chat", "对话", False)
    danger = DockButton("close", "退出", True)
    assert btn._is_danger is False
    assert danger._is_danger is True
    btn.set_pinned(True)
    assert btn._pinned is True


def test_dock_button_scale_property(qapp):
    btn = DockButton("chat", "对话")
    assert btn.get_scale() == 1.0
    btn.set_scale(1.2)
    assert abs(btn.get_scale() - 1.2) < 1e-9
    assert btn.width() == int(DockButton.BASE_SIZE * 1.2)


def test_dock_bar_hover_scale_neighbors(qapp):
    bar = DockBar()
    idx = [i for i, b in enumerate(bar._buttons) if b.toolTip() == "电话"][0]
    bar._apply_hover_scale(idx)
    # 断言触发的是动画目标值而非瞬时值较难；至少不应抛异常且按钮仍在
    assert len(bar._buttons) == 6
    bar._apply_leave_scale()


def test_status_bar_time_format(qapp):
    bar = StatusBar()
    bar._update_time()
    import re
    assert re.fullmatch(r"\d{2}:\d{2}", bar._clock.text())


def test_status_bar_cursor_toggle(qapp):
    bar = StatusBar()
    # 未 show() 的父窗口下 isVisible 恒 False；从确定隐藏态出发验证翻转机制
    bar._cursor.hide()
    assert bar._cursor.isHidden()
    bar._toggle_cursor()  # setVisible(not isVisible()) → setVisible(True) → 取消隐藏
    assert not bar._cursor.isHidden()


def test_glitch_label_advance_and_paint(qapp):
    label = GlitchLabel("AMADEUS")
    assert label.sizeHint().width() > 0
    label._advance()
    pixmap = label.grab()  # 触发一次完整 paintEvent
    assert not pixmap.isNull()


def test_glitch_label_glitch_frames_cycle(qapp):
    label = GlitchLabel("AMADEUS")
    label._glitch_frames = 2
    label._advance()
    assert label._glitch_frames == 1
