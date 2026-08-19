"""Non-blocking speech output with configurable TTS providers and SAPI fallback."""
from __future__ import annotations

import io
import queue
import re
import threading
import time
import wave

from PySide6.QtCore import QObject, Signal


# 情绪 → 语速系数：angry 快、sad 慢，差异足够让听感区分情绪。
# 线性重采样同时改变速度与音高（angry 更高更急、sad 更低更缓），更贴近真人语气。
_EMOTION_SPEED: dict[str, float] = {
    "neutral": 1.0,
    "smile": 1.08,
    "angry": 1.18,
    "sad": 0.85,
    "blush": 0.94,
    "thinking": 1.0,
}


def emotion_speed(emotion: str | None) -> float:
    """把情绪标签映射为语速系数（未知情绪回退 1.0）。"""
    return _EMOTION_SPEED.get((emotion or "neutral").strip().lower(), 1.0)


class SpeechPlayer(QObject):
    speaking_changed = Signal(bool)
    # 播放音量强度（0.0-1.0），播放线程按 50ms chunk 算 RMS 发射，
    # Live2D 端据此驱动口型开合（setMouth），实现"嘴型跟音频音量走"。
    mouth_intensity = Signal(float)
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
        self._emotion = "neutral"
        self._speed = 1.0
        self._stop_event = threading.Event()
        self._kurisu_available: bool | None = None
        self._available_checked_at: float = 0.0
        self._available_provider: str | None = None
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
        emotion: str | None = None,
    ) -> None:
        if not text:
            return
        self.stop()
        self._stop_event.clear()
        self._emotion = emotion or "neutral"
        self._speed = emotion_speed(self._emotion)
        threading.Thread(
            target=self._speak_worker,
            args=(text, text_lang, prompt_text, prompt_lang, allow_fallback),
            daemon=True,
        ).start()

    def speak_streaming_start(self, text_lang: str | None = "ja", emotion: str | None = None) -> None:
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
        self._emotion = emotion or "neutral"
        self._speed = emotion_speed(self._emotion)
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
            provider = self._get_tts_provider()
            if self._available_expired():
                self._kurisu_available = self._check_provider_available(provider)
                self._available_checked_at = time.monotonic()
            if not self._kurisu_available:
                self.tts_offline.emit()
                print(f"[SpeechPlayer] {provider} offline, streaming disabled")
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
            # GPT-SoVITS 首句用 cut1；云端 provider 忽略 is_first。
            is_first_sentence = True
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
                self._synthesize_and_enqueue(sentence, text_lang, is_first=is_first_sentence)
                is_first_sentence = False
            # 兜底：会话被 stop 打断时也要通知播放 worker 结束
            self._playback_queue.put(None)
            # 等播放 worker 结束（最多 30s，防止卡死）
            playback_thread.join(timeout=30.0)
        finally:
            self.speaking_changed.emit(False)

    def _synthesize_and_enqueue(
        self, sentence: str, text_lang: str | None, is_first: bool = False
    ) -> None:
        """合成一句并放入 _playback_queue（供播放 worker 取走播放）。

        is_first=True 时用 cut1（不切分整段推理），跳过 batch 调度开销。
        首句已合并到 ~14 字短句，cut5 切分后段数 ≤2 用不上 batch_size=5，
        cut1 单段推理更快。后续句用 cut5 切分以利用 batch_size=5 并行。

        aliyun + CosyVoice 路径走流式合成：_synthesize_aliyun_stream 生成器 yield PCM chunks，
        塞给 _playback_worker 调 _play_wav_stream 边收边播（与 amadeus <audio src=url> 流式播放等价）。
        gpt_sovits 路径走非流式：合成完整 wav bytes 直接塞队列。
        """
        if self._stop_event.is_set() or not sentence:
            return
        provider = self._get_tts_provider()
        if provider == "aliyun":
            # 流式路径：把生成器塞队列，_playback_worker 取出后调 _play_wav_stream 迭代播放
            stream_iter = self._synthesize_aliyun_stream(sentence, text_lang=text_lang)
            self._playback_queue.put(("stream", stream_iter))
        else:
            ok, wav_bytes = self._synthesize_kurisu(
                sentence,
                text_lang=text_lang,
                prompt_text=None,
                prompt_lang="ja",
                is_first=is_first,
            )
            if not ok or not wav_bytes:
                print(f"[SpeechPlayer] streaming sentence failed: {sentence[:30]}")
                return
            self._playback_queue.put(("wav", wav_bytes))

    def _playback_worker(self) -> None:
        """播放 worker：从 _playback_queue 取 wav/stream 按顺序播放。

        独立线程，与合成循环并行。队列元素：
        - None：会话结束信号
        - ("wav", bytes)：完整 wav bytes，调 _play_wav 播放（gpt_sovits 路径）
        - ("stream", iter)：流式生成器，调 _play_wav_stream 边收边播（aliyun 路径）
        - bytes：兼容旧调用，当作完整 wav 处理

        双缓冲预取：合成线程把流式 iter/wav 塞队列后立即去合成下一句，
        本线程顺序取出播放。合成快时预取多句堆队列，播放快时合成已就绪。
        """
        while not self._stop_event.is_set():
            try:
                item = self._playback_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            if self._stop_event.is_set():
                break
            # 兼容旧调用（直接 bytes）：当作完整 wav 处理
            if isinstance(item, bytes):
                self.playback_started.emit(self._wav_duration_seconds(item))
                self._play_wav(item)
                continue
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            kind, payload = item
            if kind == "stream":
                # 流式播放：迭代生成器，按 ("pcm", chunk) / ("wav", bytes) 元组分发
                self.playback_started.emit(0.0)  # 流式路径无法预算时长
                self._play_wav_stream(payload)
            else:  # "wav"
                self.playback_started.emit(self._wav_duration_seconds(payload))
                self._play_wav(payload)

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

    def _get_tts_provider(self) -> str:
        try:
            from config import TTS_PROVIDER_DEFAULT
            from core.storage import load_config
            provider = str(load_config().get("tts_provider", TTS_PROVIDER_DEFAULT))
        except Exception:
            provider = "gpt_sovits"
        provider = provider if provider in ("gpt_sovits", "aliyun") else "gpt_sovits"
        if provider != self._available_provider:
            self._kurisu_available = None
            self._available_checked_at = 0.0
            self._available_provider = provider
        return provider

    def _check_provider_available(self, provider: str) -> bool:
        if provider != self._available_provider:
            self._kurisu_available = None
            self._available_checked_at = 0.0
            self._available_provider = provider
        if provider == "aliyun":
            try:
                from config import ALIYUN_TTS_DEFAULTS
                from core.storage import load_config
                cfg = {**ALIYUN_TTS_DEFAULTS, **(load_config().get("aliyun_tts") or {})}
                return bool(str(cfg.get("api_key", "")).strip() and str(cfg.get("voice_id", "")).strip())
            except Exception:
                return False
        return self._check_kurisu()

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
            provider = self._get_tts_provider()
            if self._available_expired():
                self._kurisu_available = self._check_provider_available(provider)
                self._available_checked_at = time.monotonic()
            spoke = False
            if self._kurisu_available:
                if provider == "aliyun":
                    spoke = self._speak_aliyun(text, text_lang=text_lang)
                else:
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
                    print(f"[SpeechPlayer] {provider} offline, no fallback allowed")
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

    def _speak_aliyun(self, text: str, *, text_lang: str | None = None) -> bool:
        """合成并播放阿里云 TTS（阻塞直到播放完成）。"""
        try:
            ok, wav_bytes = self._synthesize_aliyun(text, text_lang=text_lang)
            if not ok or not wav_bytes or self._stop_event.is_set():
                return False
            self.playback_started.emit(self._wav_duration_seconds(wav_bytes))
            self._play_wav(wav_bytes)
            return True
        except Exception as exc:
            print(f"[SpeechPlayer] Aliyun TTS failed: {exc}")
            return False

    def _synthesize_kurisu(
        self,
        text: str,
        *,
        text_lang: str | None = None,
        prompt_text: str | None = None,
        prompt_lang: str | None = None,
        is_first: bool = False,
    ) -> tuple[bool, bytes | None]:
        """只合成不播放，返回 (success, wav_bytes)。供流式合成使用。

        is_first=True 时传 text_split_method='cut1'（不切分整段推理）。
        """
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
                text_split_method="cut1" if is_first else None,
            )
            if not wav_bytes or self._stop_event.is_set():
                return False, None
            return True, wav_bytes
        except Exception as exc:
            print(f"[SpeechPlayer] GPT-SoVITS synthesize failed: {exc}")
            return False, None

    def _clean_tts_text(self, text: str) -> str:
        """清理 TTS 文本，修复 CosyVoice 末尾拖音问题。

        移植自 amadeus src/lib/tts.ts:305-322 cleanTTS_text：
        1. 去掉省略号（中英文、连续句点）→ 替换为单句号
        2. 去掉波浪号（会被引擎读成颤音）
        3. 合并多余换行/空格 → 单空格
        4. 末尾确保有句号（让引擎明确句子结束，避免自动补拖音）

        物理意义：CosyVoice 引擎对末尾标点敏感，缺句号会自动补拖音，
        省略号会被读成 "yi" 等奇怪音，波浪号会被读成颤音。
        """
        if not text:
            return ""
        t = text
        # 0. 去掉括号内的动作/语气词（如「（歪头微笑）」「(thinking)」），
        #    这些是 LLM 给 Live2D 的舞台指示，不应被 TTS 读出来。
        t = re.sub(r"（[^（）]*）", "", t)
        t = re.sub(r"\([^()]*\)", "", t)
        # 1. 去掉省略号（中英文、连续句点）
        t = re.sub(r"\.{3,}", "。", t)
        t = t.replace("…", "。")
        t = re.sub(r"。{2,}", "。", t)
        # 2. 去掉波浪号（会被读成颤音）
        t = t.replace("~", "").replace("〜", "")
        # 3. 合并多余换行/空格
        t = re.sub(r"\s+", " ", t).strip()
        # 4. 末尾确保有句号（已有标点则不加）
        if t and not re.search(r"[。！？!?\.]$", t):
            t = t + "。"
        return t

    def _synthesize_aliyun(
        self,
        text: str,
        *,
        text_lang: str | None = None,
    ) -> tuple[bool, bytes | None]:
        """阿里云 TTS 合成（非流式），返回 (success, wav_bytes)。

        根据 engine 选择路径（与 amadeus src/app/api/tts/route.ts:158-311 对齐）：
        - qwen3-tts-vc：调 multimodal-generation/generation endpoint，返回 OSS URL → 下载 mp3
        - cosyvoice-*：调 SpeechSynthesizer endpoint，返回 OSS URL → 下载 mp3（非流式回退路径）

        流式路径（CosyVoice 边下边播）走 _synthesize_aliyun_stream。
        所有路径都先调 _clean_tts_text 修复 CosyVoice 末尾拖音。
        """
        try:
            from config import ALIYUN_TTS_DEFAULTS
            from core.aliyun_tts_client import AliyunTTS
            from core.mp3_decoder import decode_mp3_to_wav
            from core.storage import load_config

            cfg = {**ALIYUN_TTS_DEFAULTS, **(load_config().get("aliyun_tts") or {})}
            api_key = str(cfg.get("api_key", "")).strip()
            voice_id = str(cfg.get("voice_id", "")).strip()
            engine = str(cfg.get("engine", ALIYUN_TTS_DEFAULTS["engine"])).strip() or "cosyvoice-v3.5-flash"
            model = str(cfg.get("model", ALIYUN_TTS_DEFAULTS["model"])).strip()
            timeout = float(cfg.get("timeout", 30) or 30)
            if not api_key or not voice_id:
                return False, None
            clean_text = self._clean_tts_text(text)
            if not clean_text:
                return False, None
            tts = AliyunTTS(api_key, timeout=timeout)
            if engine == "qwen3-tts-vc":
                # Qwen3-TTS-VC 路径：调 multimodal-generation/generation，返回 OSS URL → 下载完整 mp3
                mp3_bytes = tts.synthesize(
                    clean_text,
                    voice_id,
                    text_lang=text_lang or "ja",
                    model=model,
                )
            else:
                # CosyVoice 路径（非流式回退）：调 SpeechSynthesizer，返回 OSS URL → 下载完整 mp3
                # 流式路径走 _synthesize_aliyun_stream（OSS URL 透传给播放层边下边播）
                url = tts.synthesize_cosyvoice(clean_text, voice_id, engine=engine)
                if not url or self._stop_event.is_set():
                    return False, None
                mp3_bytes = tts._get_bytes(url)
            if not mp3_bytes or self._stop_event.is_set():
                return False, None
            wav_bytes = decode_mp3_to_wav(mp3_bytes)
            if not wav_bytes or self._stop_event.is_set():
                return False, None
            return True, wav_bytes
        except Exception as exc:
            print(f"[SpeechPlayer] Aliyun TTS synthesize failed: {exc}")
            return False, None

    def _synthesize_aliyun_stream(self, text: str, text_lang: str | None = None):
        """阿里云 CosyVoice 流式合成生成器，yield PCM int16 bytes chunks。

        与 amadeus src/app/api/tts/route.ts:288-291 OSS URL 透传策略一致：
        - synthesize_cosyvoice 返回 OSS URL（不在 client 层下载）
        - decode_mp3_stream 用 _HttpStreamableSource + miniaudio.stream_any 流式解码
        - 上层 _play_wav_stream 用 sounddevice 边收边播

        Qwen3-TTS-VC 引擎不支持流式（OSS URL 必须下载完整 mp3），自动回退到非流式：
        一次性 yield 完整 wav bytes 给 _play_wav_stream（_play_wav_stream 识别 wav header 跳过）。

        数学本质：三段管道并行（HTTP 下载 → 解码 → 播放），总时延 ≈ max(下载, 解码) + 单 chunk 播放时间，
        相比串行省 min(下载, 解码) 时间，与 amadeus 浏览器 <audio src=url> 流式播放等价。
        """
        try:
            from config import ALIYUN_TTS_DEFAULTS
            from core.aliyun_tts_client import AliyunTTS
            from core.mp3_decoder import decode_mp3_stream
            from core.storage import load_config

            cfg = {**ALIYUN_TTS_DEFAULTS, **(load_config().get("aliyun_tts") or {})}
            api_key = str(cfg.get("api_key", "")).strip()
            voice_id = str(cfg.get("voice_id", "")).strip()
            engine = str(cfg.get("engine", ALIYUN_TTS_DEFAULTS["engine"])).strip() or "cosyvoice-v3.5-flash"
            timeout = float(cfg.get("timeout", 30) or 30)
            if not api_key or not voice_id:
                return
            if engine == "qwen3-tts-vc":
                # Qwen3-TTS-VC 不支持流式：回退到非流式（一次性 yield 完整 wav bytes）
                # _play_wav_stream 通过 wave header 识别 wav bytes
                ok, wav_bytes = self._synthesize_aliyun(text, text_lang=text_lang)
                if ok and wav_bytes:
                    yield ("wav", wav_bytes)  # 标记是 wav bytes，_play_wav_stream 用 _play_wav 播放
                return
            clean_text = self._clean_tts_text(text)
            if not clean_text:
                return
            tts = AliyunTTS(api_key, timeout=timeout)
            url = tts.synthesize_cosyvoice(clean_text, voice_id, engine=engine, speech_rate=self._speed)
            if not url:
                return
            # 流式解码 OSS URL → PCM int16 chunks（标记 "pcm"）
            for chunk in decode_mp3_stream(url, sample_rate=24000, timeout=timeout):
                if self._stop_event.is_set():
                    return
                yield ("pcm", chunk)
        except Exception as exc:
            print(f"[SpeechPlayer] Aliyun TTS stream failed: {exc}")

    def _wav_duration_seconds(self, wav_bytes: bytes) -> float:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            frames = wav.getnframes()
            framerate = wav.getframerate() or 1
            return frames / float(framerate)

    def _play_wav(self, wav_bytes: bytes) -> None:
        import numpy as np
        import sounddevice as sd

        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            sr = wav.getframerate()
            channels = wav.getnchannels()
            frames = wav.readframes(wav.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32767.0
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)

        # 情绪化语速：angry 快、sad 慢。线性重采样同时改变速度与音高，
        # 与情绪标签对应（angry 更急更高、sad 更缓更低）。
        speed = self._speed or 1.0
        if speed != 1.0 and audio.size > 1:
            n = audio.size
            new_n = max(1, int(round(n / speed)))
            audio = np.interp(
                np.linspace(0.0, 1.0, new_n, endpoint=False),
                np.linspace(0.0, 1.0, n, endpoint=False),
                audio,
            ).astype(np.float32)

        chunk_size = max(1, int(sr * 0.05))
        with sd.OutputStream(samplerate=sr, channels=1, dtype="float32") as stream:
            for idx in range(0, len(audio), chunk_size):
                if self._stop_event.is_set():
                    break
                chunk = audio[idx : idx + chunk_size]
                stream.write(chunk.reshape(-1, 1))
                # 音量 → 口型强度：RMS 经验缩放 ×4 映射到 0-1（静音≈0，正常说话≈0.3-0.8）
                rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2))) if chunk.size else 0.0
                self.mouth_intensity.emit(min(1.0, rms * 4.0))
        self.mouth_intensity.emit(0.0)

    def _play_wav_stream(self, stream_iter) -> None:
        """流式播放生成器，按 ("pcm", chunk) / ("wav", bytes) 元组分发。

        与 _play_wav 区别：不需要预先有完整 wav，边收 PCM 边喂 sounddevice OutputStream。
        用于阿里云 CosyVoice 流式合成（OSS URL 边下边解码边播放）。

        Qwen3-TTS-VC 回退路径在生成器内 yield ("wav", bytes) 完整 wav，本方法识别后调 _play_wav 播放。
        CosyVoice 流式路径 yield ("pcm", chunk) PCM int16 bytes，本方法边收边喂 sounddevice。

        数学本质：三段管道（HTTP 下载 → 解码 → 播放）并行，
        总时延 ≈ max(下载, 解码) + 单 chunk 播放时间。形象理解：水管模型，
        水龙头（HTTP）→ 过滤器（解码）→ 水杯（播放），三段同时工作不互相等待。
        """
        import numpy as np
        import sounddevice as sd

        stream = None
        try:
            for kind, payload in stream_iter:
                if self._stop_event.is_set():
                    break
                if kind == "wav":
                    # 完整 wav bytes（Qwen3-TTS-VC 回退路径）：用 _play_wav 播放
                    self.playback_started.emit(self._wav_duration_seconds(payload))
                    self._play_wav(payload)
                    continue
                # kind == "pcm"：PCM int16 chunks，边收边喂 sounddevice
                if stream is None:
                    stream = sd.OutputStream(samplerate=24000, channels=1, dtype="int16")
                    stream.start()
                if not payload:
                    continue
                audio = np.frombuffer(payload, dtype=np.int16)
                if audio.size == 0:
                    continue
                stream.write(audio.reshape(-1, 1))
                # 音量 → 口型强度：int16 转 float32 后算 RMS，经验缩放 ×4 映射 0-1
                rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) / 32767.0
                self.mouth_intensity.emit(min(1.0, rms * 4.0))
        except Exception as exc:
            print(f"[SpeechPlayer] stream playback failed: {exc}")
        finally:
            # 会话/流结束：口型归零（renderer 收到 0 闭嘴）
            if stream is not None:
                self.mouth_intensity.emit(0.0)
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass

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
