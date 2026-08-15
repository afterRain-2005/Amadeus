"""Non-blocking speech output with GPT-SoVITS first and SAPI fallback."""
from __future__ import annotations

import io
import threading
import time
import wave

import numpy as np
from PySide6.QtCore import QObject, Signal


class SpeechPlayer(QObject):
    speaking_changed = Signal(bool)
    playback_started = Signal(float)
    # GPT-SoVITS 不可用且无 SAPI 兜底时发射（UI 层据此提示「语音服务离线」）
    tts_offline = Signal()

    # 可用性缓存 TTL：不可用时每隔 60s 重查一次，API 中途启动可自愈，
    # 不必重启桌宠（曾因永久缓存 False 导致整轮会话无声）。
    _AVAILABLE_TTL = 60.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rate = 0
        self._stop_event = threading.Event()
        self._kurisu_available: bool | None = None
        self._available_checked_at: float = 0.0

    def set_rate(self, rate: int) -> None:
        self.rate = rate

    def speak(self, text: str) -> None:
        self.speak_with_options(text)

    def speak_with_options(
        self,
        text: str,
        *,
        text_lang: str | None = None,
        prompt_text: str | None = None,
        prompt_lang: str | None = None,
        allow_fallback: bool = False,
    ) -> None:
        if not text:
            return
        self.stop()
        self._stop_event.clear()
        threading.Thread(
            target=self._speak_worker,
            args=(text, text_lang, prompt_text, prompt_lang, allow_fallback),
            daemon=True,
        ).start()

    def stop(self) -> None:
        self._stop_event.set()

    def _check_kurisu(self) -> bool:
        try:
            from core.gpt_sovits_client import KurisuTTS
            return KurisuTTS().available
        except Exception:
            return False

    def _available_expired(self) -> bool:
        """可用性缓存是否过期需重查。可用时不重查（合成失败会翻转缓存走 TTL）。"""
        if self._kurisu_available is None:
            return True
        if self._kurisu_available:
            return False
        return (time.monotonic() - self._available_checked_at) > self._AVAILABLE_TTL

    def _speak_worker(
        self,
        text: str,
        text_lang: str | None = None,
        prompt_text: str | None = None,
        prompt_lang: str | None = None,
        allow_fallback: bool = False,
    ) -> None:
        self.speaking_changed.emit(True)
        try:
            if self._available_expired():
                self._kurisu_available = self._check_kurisu()
                self._available_checked_at = time.monotonic()
            spoke = False
            if self._kurisu_available:
                spoke = self._speak_kurisu(
                    text,
                    text_lang=text_lang,
                    prompt_text=prompt_text,
                    prompt_lang=prompt_lang,
                )
                if not spoke and not self._stop_event.is_set():
                    # 真实失败（非用户打断）：翻转缓存，等待 TTL 重查自愈
                    self._kurisu_available = False
                    self._available_checked_at = time.monotonic()
            if not spoke and not self._stop_event.is_set():
                if allow_fallback:
                    self._speak_sapi_blocking(text)
                else:
                    self.tts_offline.emit()
                    print("[SpeechPlayer] GPT-SoVITS offline, no fallback allowed")
        finally:
            self.speaking_changed.emit(False)

    def _speak_kurisu(
        self,
        text: str,
        *,
        text_lang: str | None = None,
        prompt_text: str | None = None,
        prompt_lang: str | None = None,
    ) -> bool:
        try:
            from core.gpt_sovits_client import KurisuTTS

            tts = KurisuTTS()
            if not tts.available:
                return False
            wav_bytes = tts.synthesize(
                text,
                text_lang=text_lang,
                prompt_text=prompt_text,
                prompt_lang=prompt_lang,
            )
            if not wav_bytes or self._stop_event.is_set():
                return False
            self.playback_started.emit(self._wav_duration_seconds(wav_bytes))
            self._play_wav(wav_bytes)
            return True
        except Exception as exc:
            print(f"[SpeechPlayer] GPT-SoVITS failed: {exc}")
            return False

    def _wav_duration_seconds(self, wav_bytes: bytes) -> float:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            frames = wav.getnframes()
            framerate = wav.getframerate() or 1
            return frames / float(framerate)

    def _play_wav(self, wav_bytes: bytes) -> None:
        import sounddevice as sd

        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            sr = wav.getframerate()
            channels = wav.getnchannels()
            frames = wav.readframes(wav.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32767.0
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)

        chunk_size = max(1, int(sr * 0.05))
        with sd.OutputStream(samplerate=sr, channels=1, dtype="float32") as stream:
            for idx in range(0, len(audio), chunk_size):
                if self._stop_event.is_set():
                    break
                chunk = audio[idx : idx + chunk_size]
                stream.write(chunk.reshape(-1, 1))

    def _speak_sapi_blocking(self, text: str) -> None:
        if self._stop_event.is_set():
            return
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