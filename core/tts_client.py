"""Non-blocking speech output：GPT-SoVITS（红莉栖音色）→ SAPI 降级。

优先级：
  1. GPT-SoVITS V3 少样本推理（KurisuTTS）— 红莉栖真音色
  2. Windows SAPI — 系统语音降级（无 GPT-SoVITS 时自动切换）
"""
from __future__ import annotations

import io
import threading
import wave

import numpy as np
from PySide6.QtCore import QObject, Signal


class SpeechPlayer(QObject):
    """语音播放器：GPT-SoVITS 优先，SAPI 降级。

    speaking_changed 信号：True=开始说话，False=说完了。
    用于驱动 Live2D 口型动画。
    """

    speaking_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rate = 0
        self._stop_event = threading.Event()
        self._kurisu_available: bool | None = None  # None=未检测

    def set_rate(self, rate: int) -> None:
        self.rate = rate

    def speak(self, text: str) -> None:
        if not text:
            return

        # 优先 GPT-SoVITS
        if self._kurisu_available is None:
            self._kurisu_available = self._check_kurisu()
        if self._kurisu_available:
            self._speak_kurisu(text)
            return

        # 降级 SAPI
        self._speak_sapi(text)

    def stop(self) -> None:
        """停止当前播放。"""
        self._stop_event.set()

    # ===== GPT-SoVITS 路径 =====
    def _check_kurisu(self) -> bool:
        try:
            from core.gpt_sovits_client import KurisuTTS
            return KurisuTTS().available
        except Exception:
            return False

    def _speak_kurisu(self, text: str) -> None:
        self._stop_event.clear()

        def run() -> None:
            self.speaking_changed.emit(True)
            try:
                from core.gpt_sovits_client import KurisuTTS
                tts = KurisuTTS()
                wav_bytes = tts.synthesize(text)
                if wav_bytes and not self._stop_event.is_set():
                    self._play_wav(wav_bytes)
            except Exception:
                # GPT-SoVITS 失败，降级 SAPI
                self._speak_sapi(text)
            finally:
                self.speaking_changed.emit(False)

        threading.Thread(target=run, daemon=True).start()

    def _play_wav(self, wav_bytes: bytes) -> None:
        """播放 wav bytes（sounddevice 输出）。"""
        import sounddevice as sd
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            sr = w.getframerate()
            frames = w.readframes(w.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32767.0

        chunk_ms = 50       # 50ms 分块播放，支持打断
        chunk_size = int(sr * chunk_ms / 1000)
        idx = 0
        sd.default.samplerate = sr
        stream = sd.OutputStream(samplerate=sr, channels=1, dtype="float32")
        stream.start()
        try:
            while idx < len(audio):
                if self._stop_event.is_set():
                    break
                end = min(idx + chunk_size, len(audio))
                chunk = audio[idx:end]
                if len(chunk) < chunk_size:
                    chunk = np.pad(chunk, (0, chunk_size - len(chunk)))
                stream.write(chunk.reshape(-1, 1))
                idx = end
        finally:
            stream.stop()
            stream.close()

    # ===== SAPI 降级路径 =====
    def _speak_sapi(self, text: str) -> None:
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