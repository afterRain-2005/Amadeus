"""Shared CRT dialog title bar regression tests."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from ui.widgets.crt_title_bar import CRT_TITLE_BAR_QSS, CrtTitleBar


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_title_bar_exposes_shared_structure(qapp):
    dialog = QDialog()
    title_bar = CrtTitleBar("Terminal", "wired", dialog)

    assert title_bar.objectName() == "crtTitleBar"
    assert title_bar.title_label.objectName() == "crtTitle"
    assert title_bar.signature_label.objectName() == "crtSignature"
    assert title_bar.close_button.objectName() == "crtClose"
    assert title_bar.close_button.text() == "X"
    assert not title_bar.close_button.autoDefault()


def test_title_bar_keeps_rose_frame(qapp):
    assert "border: 1px solid #d2738a" in CRT_TITLE_BAR_QSS
    assert "border-left: 8px solid #d2738a" in CRT_TITLE_BAR_QSS


def test_title_bar_updates_signature_and_closes(qapp):
    dialog = QDialog()
    title_bar = CrtTitleBar("Settings", "phase one", dialog, dialog.reject)

    title_bar.set_signature("phase two")
    assert title_bar.signature_label.text() == "phase two"

    dialog.show()
    title_bar.close_button.click()
    assert not dialog.isVisible()
