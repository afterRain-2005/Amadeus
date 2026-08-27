# tests/test_call_view.py
from unittest.mock import MagicMock
from PySide6.QtWidgets import QApplication
import pytest


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_call_view_constructs(app):
    from ui.widgets.call_view import CallView
    view = CallView()
    assert view is not None


def test_call_view_has_three_buttons(app):
    """底部三按钮：🎤 麦克风 / ✕ 挂断 / 🖥 屏幕共享。"""
    from ui.widgets.call_view import CallView
    view = CallView()
    assert hasattr(view, "mute_btn")
    assert hasattr(view, "hangup_btn")
    assert hasattr(view, "screen_btn")


def test_call_view_updates_subtitle(app):
    from ui.widgets.call_view import CallView
    view = CallView()
    view.set_subtitle("聆听中")
    assert "聆听中" in view.subtitle_label.text()


def test_call_view_updates_phase_status(app):
    """状态条随 phase 变化显示对应文案。"""
    from ui.widgets.call_view import CallView
    view = CallView()
    view.set_phase("connecting")
    assert "接通" in view.status_label.text() or "connecting" in view.status_label.text().lower()
    view.set_phase("listening")
    assert "聆听" in view.status_label.text() or "listening" in view.status_label.text().lower()
    assert view.status_label.text().startswith("CALL/")


def test_call_view_uses_terminal_speaker_prefixes(app):
    from ui.widgets.call_view import CallView
    view = CallView()
    view.set_you_said("测试输入")
    view.set_subtitle("测试输出")
    assert view.you_said_label.text() == "you> 测试输入"
    assert view.subtitle_label.text() == "kurisu> 测试输出"


def test_call_view_buttons_emit_signals(app):
    from ui.widgets.call_view import CallView
    view = CallView()
    mute_clicked = MagicMock()
    hangup_clicked = MagicMock()
    screen_clicked = MagicMock()
    view.mute_clicked.connect(mute_clicked)
    view.hangup_clicked.connect(hangup_clicked)
    view.screen_clicked.connect(screen_clicked)
    view.mute_btn.click()
    view.hangup_btn.click()
    view.screen_btn.click()
    mute_clicked.assert_called_once()
    hangup_clicked.assert_called_once()
    screen_clicked.assert_called_once()


def test_call_view_waveform_paints(app):
    """set_waveform 不抛异常。"""
    from ui.widgets.call_view import CallView
    view = CallView()
    view.set_waveform(0.5)
    view.set_waveform(0.0)
