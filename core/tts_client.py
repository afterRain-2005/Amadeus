"""Non-blocking speech output through Windows SAPI."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal


class SpeechPlayer(QObject):
    speaking_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rate = 0

    def set_rate(self, rate: int) -> None:
        self.rate = rate

    def speak(self, text: str) -> None:
        if not text:
            return

        def run() -> None:
            self.speaking_changed.emit(True)
            try:
                import win32com.client
                voice = win32com.client.Dispatch("SAPI.SpVoice")
                voice.Rate = self.rate
                voices = voice.GetVoices()
                for index in range(voices.Count):
                    candidate = voices.Item(index)
                    description = candidate.GetDescription().lower()
                    if "japanese" in description or "haruka" in description or "ayumi" in description:
                        voice.Voice = candidate
                        break
                voice.Speak(text)
            finally:
                self.speaking_changed.emit(False)

        threading.Thread(target=run, daemon=True).start()

    def stop(self) -> None:
        pass
