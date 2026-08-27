"""Shared CRT dialog title bar regression tests."""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QDialog

from ui.theme import ROSE
from ui.widgets.crt_title_bar import CRT_TITLE_BAR_QSS, CrtTitleBar


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_title_bar_exposes_shared_structure(qapp):
    dialog = QDialog()
    title_bar = CrtTitleBar("Terminal", "wired", dialog)

    assert title_bar.objectName() == "crtTitleBar"
    assert title_bar.frame.objectName() == "crtTitleFrame"
    assert title_bar.title_label.objectName() == "crtTitle"
    assert title_bar.signature_label.objectName() == "crtSignature"
    assert title_bar.close_button.objectName() == "crtClose"
    assert title_bar.close_button.text() == "X"
    assert not title_bar.close_button.autoDefault()


def test_title_bar_keeps_rose_frame(qapp):
    assert "QWidget#crtTitleBar { background: transparent; border: 0; }" in CRT_TITLE_BAR_QSS
    assert "QWidget#crtTitleFrame" in CRT_TITLE_BAR_QSS
    assert f"border: 1px solid {ROSE}" in CRT_TITLE_BAR_QSS
    assert f"border-left: 8px solid {ROSE}" in CRT_TITLE_BAR_QSS


def test_title_uses_lainos_heading_style(qapp):
    title_bar = CrtTitleBar("Amadeus Terminal", "wired")
    font = title_bar.title_label.font()

    assert title_bar.title_label.text() == "Amadeus Terminal"
    assert font.family() == "Courier New"
    assert font.weight() == QFont.Bold
    assert font.letterSpacingType() == QFont.AbsoluteSpacing
    assert font.letterSpacing() == 1.0
    assert title_bar.title_label.graphicsEffect() is None
    assert f"QLabel#crtTitle {{ color: {ROSE};" in CRT_TITLE_BAR_QSS


def test_dialogs_keep_outer_rose_frames():
    settings_source = (ROOT / "ui" / "settings_dialog.py").read_text(encoding="utf-8")
    terminal_source = (ROOT / "ui" / "widgets" / "agent_terminal.py").read_text(encoding="utf-8")

    assert "QDialog#settingsDialog" in settings_source
    assert f"border: 1px solid {{ROSE}}" in settings_source
    assert '"Amadeus Settings"' in settings_source
    assert "QDialog#agentTerminal" in terminal_source
    assert f"border:1px solid {{_TERMINAL_ROSE}}" in terminal_source
    assert '"Amadeus Terminal"' in terminal_source


def test_title_bar_updates_signature_and_closes(qapp):
    dialog = QDialog()
    title_bar = CrtTitleBar("Settings", "phase one", dialog, dialog.reject)

    title_bar.set_signature("phase two")
    assert title_bar.signature_label.text() == "phase two"

    dialog.show()
    title_bar.close_button.click()
    assert not dialog.isVisible()
