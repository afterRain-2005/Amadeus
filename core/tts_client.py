"""Non-blocking speech output with GPT-SoVITS first and SAPI fallback."""
from __future__ import annotations

import io
import queue
import re
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

    # 流式合成：句末标点（日语/中文/英文），遇到即送 TTS
    _SENTENCE_END_RE = re.compile(r"[。！？!?\n]")

    # 合并阈值：所有句子进 _merge_buffer 累积，达阈值送队列。
    # _MERGE_THRESHOLD=14：首句合并目标（保首句延迟 ~4s，14 字 S=4.06 P=4.43 S<P）
    # _MERGE_UPPER=32：后续句合并上限（减少段数，避免 TTS 听感太碎）
    # 物理依据：14 字是双缓冲 S<P 临界点；32 字合成 ~5-6s 仍 S<P（播放 8-10s），
    # 且 cut5 切分后段长 5-15 字相似，batch_size=5 并行有效（lessons 8-15 教训 1b）。
    _MERGE_THRESHOLD = 14
    _MERGE_UPPER = 32

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rate = 0
        self._stop_event = threading.Event()
        self._kurisu_available: bool | None = None
        self._available_checked_at: float = 0.0
        # 流式合成状态
        self._stream_buffer = ""
        self._stream_queue: queue.Queue[tuple[str, str | None] | None] = queue.Queue()
        self._stream_thread: threading.Thread | None = None
        # 双缓冲播放队列：合成线程往里塞 wav，播放线程从中取 wav 播放
        self._playback_queue: queue.Queue[bytes | None] = queue.Queue()
        self._stream_lang: str | None = "ja"
        # 合并缓冲：暂存待合并的句子，用逗号连接
        self._merge_buffer = ""
        # 首句标记：True 时按 _MERGE_THRESHOLD 送（保首句延迟），False 后按 _MERGE_UPPER 送
        self._merge_first = True

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

    def speak_streaming_start(self, text_lang: str | None = "ja") -> None:
        """开始流式合成会话：清空缓冲，启动后台消费线程。

        GPT-4o 流式输出期间，调用 speak_streaming_append(delta) 增量追加文本，
        遇到句末标点会立即送 TTS 合成播放，实现边生成边说话。
        会话结束调用 speak_streaming_end() 刷新剩余文本。
        """
        self.stop()
        self._stop_event.clear()
        self._stream_buffer = ""
        self._merge_buffer = ""
        self._merge_first = True
        # 清空队列
        while not self._stream_queue.empty():
            try:
                self._stream_queue.get_nowait()
            except queue.Empty:
                break
        self._stream_lang = text_lang
        self._stream_thread = threading.Thread(
            target=self._stream_consumer, daemon=True
        )
        self._stream_thread.start()

    def speak_streaming_append(self, delta: str) -> None:
        """流式追加文本。按句末标点切分，短句合并到 ≥14 字再送 TTS。

        短句合并策略：
        - 短句（< _MERGE_THRESHOLD 字）暂存到 _merge_buffer，用逗号连接
        - 合并后总字数 ≥ _MERGE_THRESHOLD 时送队列
        - 长句（≥ _MERGE_THRESHOLD 字）先刷新 _merge_buffer，再直接送队列

        物理依据：GPT-SoVITS 合成地板约 3-4s（与文本长度无关），
        短句播放时间 < 合成时间导致双缓冲失败。合并短句让 S < P 恢复双缓冲收益。
        用逗号连接让 cut5 切分后 batch_size=5 并行处理（实验验证）。
        """
        if not delta or self._stop_event.is_set():
            return
        self._stream_buffer += delta
        # 按句末标点切分
        for match in self._SENTENCE_END_RE.finditer(self._stream_buffer):
            sentence = self._stream_buffer[: match.end()].strip()
            self._stream_buffer = self._stream_buffer[match.end() :]
            if sentence:
                self._dispatch_sentence(sentence)
        # 剩余未结束的文本保留在 _stream_buffer

    def _dispatch_sentence(self, sentence: str) -> None:
        """分发句子：所有句子进入合并缓冲，达阈值送队列。

        首句按 _MERGE_THRESHOLD 送（保首句延迟 ~4s），后续句按 _MERGE_UPPER 送
        （减少段数，避免 TTS 听感太碎）。所有句子用逗号连接合并，让 cut5 切分后
        段长相似，batch_size=5 并行生效（lessons 8-15 教训 1b）。
        """
        bare = self._SENTENCE_END_RE.sub("", sentence).strip()
        if not bare:
            return
        self._merge_buffer += bare + "、"
        target = self._MERGE_THRESHOLD if self._merge_first else self._MERGE_UPPER
        if len(self._merge_buffer) >= target:
            self._flush_merge_buffer()
            self._merge_first = False

    def _flush_merge_buffer(self) -> None:
        """刷新合并缓冲：把暂存的短句合并后送队列。"""
        if not self._merge_buffer:
            return
        # 去掉末尾多余的逗号，加句号保持自然语气
        merged = self._merge_buffer.rstrip("、").strip()
        if merged:
            self._stream_queue.put((merged + "。", self._stream_lang))
        self._merge_buffer = ""

    def speak_streaming_end(self) -> None:
        """流式会话结束：刷新剩余缓冲，发结束信号。"""
        if self._stream_buffer.strip():
            self._dispatch_sentence(self._stream_buffer.strip())
            self._stream_buffer = ""
        self._flush_merge_buffer()
        self._stream_queue.put(None)  # 结束信号

    def _stream_consumer(self) -> None:
        """流式合成消费线程：合成与播放完全解耦，用 _playback_queue 连接。

        架构（双缓冲预取）：
        - 合成循环（本线程）：从 _stream_queue 取句 → 合成 → 放入 _playback_queue
        - 播放循环（_playback_worker 线程）：从 _playback_queue 取 wav → 播放

        数学本质：双线程独立调度，完成时间 ≈ max(ΣS_i, ΣP_i)
        相比串行（ΣS + ΣP）削减 min(ΣS, ΣP)。
        形象理解：合成线程是"厨师做菜流水线"，播放线程是"客人吃饭流水线"，
        中间用传送带（_playback_queue）连接。厨师不必等客人吃完才做下一道菜，
        传送带会自动缓冲。合成快时预取多句堆在队列里，播放快时合成已就绪。

        短句合并（逗号连接方案）：短句（< 14 字）用逗号连接合并到 ≥14 字送 TTS。
        实验验证：4 短句单独合成 13.26s、间隔 9.13s；合并逗号连接 4.30s、间隔 0s。
        关键：用逗号连接让 cut5 切分后段长相似，split_bucket 分到同桶，batch 并行生效。
        （之前撤销的方案用句号连接，段长差异大分到不同桶，batch 失效串行 14.31s。）
        """
        self.speaking_changed.emit(True)
        try:
            if self._available_expired():
                self._kurisu_available = self._check_kurisu()
                self._available_checked_at = time.monotonic()
            if not self._kurisu_available:
                self.tts_offline.emit()
                print("[SpeechPlayer] GPT-SoVITS offline, streaming disabled")
                return

            # 清空播放队列（可能残留上一轮会话的 wav）
            while not self._playback_queue.empty():
                try:
                    self._playback_queue.get_nowait()
                except queue.Empty:
                    break

            # 启动播放 worker（独立线程，从 _playback_queue 取 wav 按顺序播放）
            playback_thread = threading.Thread(
                target=self._playback_worker, daemon=True
            )
            playback_thread.start()

            # 合成循环：取句 → 合成 → 入播放队列
            while not self._stop_event.is_set():
                try:
                    item = self._stream_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if item is None:
                    # 会话结束：通知播放 worker 结束
                    self._playback_queue.put(None)
                    break
                sentence, text_lang = item
                if self._stop_event.is_set():
                    break
                # 合成并入队（与播放并行）
                self._synthesize_and_enqueue(sentence, text_lang)
            # 兜底：会话被 stop 打断时也要通知播放 worker 结束
            self._playback_queue.put(None)
            # 等播放 worker 结束（最多 30s，防止卡死）
            playback_thread.join(timeout=30.0)
        finally:
            self.speaking_changed.emit(False)

    def _synthesize_and_enqueue(self, sentence: str, text_lang: str | None) -> None:
        """合成一句并放入 _playback_queue（供播放 worker 取走播放）。"""
        if self._stop_event.is_set() or not sentence:
            return
        ok, wav_bytes = self._synthesize_kurisu(
            sentence, text_lang=text_lang, prompt_text=None, prompt_lang="ja"
        )
        if not ok or not wav_bytes:
            print(f"[SpeechPlayer] streaming sentence failed: {sentence[:30]}")
            return
        self._playback_queue.put(wav_bytes)

    def _playback_worker(self) -> None:
        """播放 worker：从 _playback_queue 取 wav 按顺序播放。

        独立线程，与合成循环并行。队列里可能有多个已合成的 wav（双缓冲预取），
        本线程只需顺序取出播放即可。遇到 None 表示会话结束。
        """
        while not self._stop_event.is_set():
            try:
                wav = self._playback_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if wav is None:
                break
            if self._stop_event.is_set():
                break
            self.playback_started.emit(self._wav_duration_seconds(wav))
            self._play_wav(wav)

    def stop(self) -> None:
        self._stop_event.set()
        # 唤醒可能阻塞在 queue.get 的合成循环
        try:
            self._stream_queue.put(None, block=False)
        except queue.Full:
            pass
        # 唤醒可能阻塞在 queue.get 的播放 worker
        try:
            self._playback_queue.put(None, block=False)
        except queue.Full:
            pass

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
        """合成并播放（阻塞直到播放完成）。用于非流式 speak_with_options。"""
        try:
            ok, wav_bytes = self._synthesize_kurisu(
                text, text_lang=text_lang, prompt_text=prompt_text, prompt_lang=prompt_lang
            )
            if not ok or not wav_bytes or self._stop_event.is_set():
                return False
            self.playback_started.emit(self._wav_duration_seconds(wav_bytes))
            self._play_wav(wav_bytes)
            return True
        except Exception as exc:
            print(f"[SpeechPlayer] GPT-SoVITS failed: {exc}")
            return False

    def _synthesize_kurisu(
        self,
        text: str,
        *,
        text_lang: str | None = None,
        prompt_text: str | None = None,
        prompt_lang: str | None = None,
    ) -> tuple[bool, bytes | None]:
        """只合成不播放，返回 (success, wav_bytes)。供流式合成使用。"""
        try:
            from core.gpt_sovits_client import KurisuTTS

            tts = KurisuTTS()
            if not tts.available:
                return False, None
            wav_bytes = tts.synthesize(
                text,
                text_lang=text_lang,
                prompt_text=prompt_text,
                prompt_lang=prompt_lang,
            )
            if not wav_bytes or self._stop_event.is_set():
                return False, None
            return True, wav_bytes
        except Exception as exc:
            print(f"[SpeechPlayer] GPT-SoVITS synthesize failed: {exc}")
            return False, None

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