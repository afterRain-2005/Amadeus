"""Companion window title bar shared with the other CRT dialogs."""
from __future__ import annotations

from PySide6.QtCore import Signal

from ui.widgets.crt_title_bar import CrtTitleBar


class StatusBar(CrtTitleBar):
    """Compact companion title bar using the exact shared CRT rendering."""
    close_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__("Amadeus", "wire ch 1", parent)
        self.close_button.clicked.connect(self.close_clicked.emit)
        self._prompt = self.title_label
        self._ch = self.signature_label
