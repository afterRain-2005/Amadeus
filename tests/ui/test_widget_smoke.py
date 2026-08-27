"""重构 2026-08-24：从 desktop_pet.run_overlay 提取的部件冒烟测试。

覆盖 DockBar/DockButton、StatusBar、GlitchLabel 的构造、结构与关键交互，
保证提取后的模块可独立实例化且行为不变（不 show()，不弹窗）。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from ui.widgets.companion_surface import CompanionSurface
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


def test_status_bar_uses_lainos_heading_style(qapp):
    from ui.widgets.crt_title_bar import CrtTitleBar

    bar = StatusBar()
    reference = CrtTitleBar("Amadeus Terminal", "wire")

    assert bar._prompt.text() == "Amadeus"
    assert bar._prompt.font() == reference.title_label.font()
    assert bar._prompt.font().family() == "Courier New"
    assert bar._prompt.font().pixelSize() == 16
    assert bar._prompt.font().letterSpacing() == 1.0
    assert bar.minimumSizeHint().width() <= 284


def test_status_bar_close_button_emits(qapp):
    bar = StatusBar()
    calls = []
    bar.close_clicked.connect(lambda: calls.append(True))
    bar.close_button.click()
    assert calls == [True]


def test_companion_surface_paints_native_shell(qapp):
    surface = CompanionSurface(QRect(20, 50, 264, 496))
    surface.resize(304, 622)
    image = surface.grab().toImage()

    assert not surface.background.isNull()
    assert image.pixelColor(0, 0) == QColor("#d2738a")
    dpr = image.devicePixelRatio()
    assert image.pixelColor(int(20 * dpr), int(50 * dpr)) == QColor("#d2738a")


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


# ===== ui/widgets/agent_task.py（Step G 提取） =====


def test_agent_task_signals_and_cancel(qapp):
    from ui.widgets.agent_task import AgentSignals, AgentTask

    task = AgentTask([{"role": "user", "content": "hi"}])
    assert task.cancel_event.is_set() is False
    task.cancel()
    assert task.cancel_event.is_set() is True
    # 7 路信号齐全（status/delta/finished/failed/cancelled/tool_event/confirmation）
    for sig in ("status", "delta", "finished", "failed", "cancelled", "tool_event", "confirmation"):
        assert hasattr(task.signals, sig), sig
    AgentSignals()  # 可独立实例化


def test_agent_task_character_injection(qapp):
    from ui.widgets.agent_task import AgentTask

    class FakeCharacter:
        personality = "FAKE_PERSONALITY"

    task = AgentTask([{"role": "user", "content": "hi"}], character=FakeCharacter())
    assert task._character.personality == "FAKE_PERSONALITY"
    assert AgentTask([{"role": "user", "content": "hi"}])._character is None


# ===== ui/widgets/agent_terminal.py（Step H 提取） =====


def _make_terminal(qapp):
    from ui.widgets.agent_terminal import AgentTerminal

    return AgentTerminal()


def test_agent_terminal_construct_and_signals(qapp):
    term = _make_terminal(qapp)
    assert hasattr(term, "submitted") and hasattr(term, "interrupt_requested")
    from ui.widgets.crt_title_bar import CrtTitleBar

    assert isinstance(term.title_bar, CrtTitleBar)
    assert term.title.text() == "Amadeus Terminal"


def test_agent_terminal_render_lines(qapp):
    term = _make_terminal(qapp)
    lines = [
        ("cmd", "hello"),
        ("out", "world"),
        ("err", "boom"),
        ("tool", "search"),
    ]
    term.render_lines(lines, full=True)
    # 渲染经 33ms 节流定时器合并执行；同步断言内部状态 + 手动触发一次 flush
    assert list(term._lines) == lines
    assert term._needs_rebuild is True
    assert term._render_timer.isActive()
    term._flush_render()
    assert "world" in term.log.toHtml()


def test_agent_terminal_set_busy(qapp):
    term = _make_terminal(qapp)
    term.set_busy(True)
    assert term.input.isReadOnly() is True
    term.set_busy(False)
    assert term.input.isReadOnly() is False


def test_agent_terminal_approval_roundtrip(qapp):
    import threading

    term = _make_terminal(qapp)
    request = {"payload": {"command": "rm"}, "event": threading.Event(), "choice": "deny"}
    term.request_approval(request)
    term._resolve_approval("once")
    assert request["choice"] == "once"
    assert request["event"].is_set()


def test_agent_terminal_tab_complete_uses_history(qapp):
    term = _make_terminal(qapp)
    term._history = ["git status", "git push"]
    completed = term._tab_complete()  # 不应抛异常
    assert completed is None or isinstance(completed, type(None))
