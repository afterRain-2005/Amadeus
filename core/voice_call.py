"""VoiceCallController：电话模式状态机 + 语音管线编排。

移植原项目 amadeus/src/components/VoiceCall.tsx 的 VAD + 回合制 STT + 状态机，
新增 mss 截帧 + GPT-4o 视觉的屏幕共享旁路。

状态机：connecting → listening → processing → speaking → listening(循环) → ended
半双工：speaking/processing 态暂停 VAD（移植 speakingRef），避免她的声音从麦克风
        回流被误判为用户说话。
屏幕附帧：utterance end 时取最新缓存帧给视觉模型，频率=说话频率（省钱）。

TTS 降级：StreamingTTS(UI redesign §7.3) 未实现，先用 SAPI SpeechPlayer。
          speaking_changed 信号判断播放结束 → 回 listening。
"""
from __future__ import annotations

from collections.abc import Callable
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from config import PHONE_DEFAULTS, VAD_PARAMS, get_character_by_id, KURISU_OUTPUT_FORMAT
from core.agent_client import _load_soul_md, _stream_turn_direct
from core.asr_client import encode_wav, transcribe
from core.emotion_parser import parse_reply
from core.screen_capture import ScreenCapturer
from core.vad import VADDetector
from core.vision_client import describe_screen


CONNECTING_MS = 1300  # "正在接通"动画时长（移植原项目）


class VoiceCallController(QObject):
    """电话模式控制器：状态机 + 管线编排。UI 通过信号驱动。"""

    phase_changed = Signal(str)       # idle/connecting/listening/processing/speaking/ended
    subtitle = Signal(str)            # 字幕文本
    you_said = Signal(str)            # 用户说的话
    waveform = Signal(float)          # 波形振幅 0-1
    elapsed = Signal(int)             # 通话秒数
    error = Signal(str)               # 错误提示
    screen_frame = Signal(object)     # 屏幕缩略图（给 UI 显示）

    def __init__(self, config: dict, character=None, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._character = character or get_character_by_id("kurisu")
        self._phase = "idle"
        self._vad = VADDetector(params=VAD_PARAMS)
        self._capturer = ScreenCapturer(
            interval_ms=int(PHONE_DEFAULTS.get("capture_interval_ms", 2500))
        )
        self._stream = None            # sounddevice InputStream
        self._recording_buf: list[np.ndarray] = []
        self._vad_paused = False       # 半双工：speaking/processing 态暂停 VAD
        self._muted = False
        self._screen_share_on = bool(PHONE_DEFAULTS.get("screen_share_default", True))
        self._elapsed_seconds = 0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._connecting_timer = QTimer(self)
        self._connecting_timer.setSingleShot(True)
        self._connecting_timer.timeout.connect(self._enter_listening)
        self._last_user_message = ""
        self._soul_md = _load_soul_md("kurisu") or self._character.personality
        # TTS：先用 SAPI SpeechPlayer 降级，StreamingTTS 就绪后切换
        from core.tts_client import SpeechPlayer
        self._tts = SpeechPlayer(self)
        self._tts.speaking_changed.connect(self._on_tts_speaking_changed)

    # ===== 属性 =====
    @property
    def phase(self) -> str:
        return self._phase

    @property
    def vad_paused(self) -> bool:
        return self._vad_paused

    @property
    def is_muted(self) -> bool:
        return self._muted

    @property
    def screen_share_on(self) -> bool:
        return self._screen_share_on

    # ===== 状态机 =====
    def _set_phase(self, phase: str) -> None:
        self._phase = phase
        # 半双工：speaking/processing 暂停 VAD（移植 speakingRef）
        self._vad_paused = phase in ("speaking", "processing")
        if phase in ("speaking", "processing", "ended"):
            self._vad.reset()
        self.phase_changed.emit(phase)

    def start(self) -> None:
        """启动通话：connecting → (1.3s) → listening。"""
        self._set_phase("connecting")
        self.subtitle.emit("正在接通…")
        self._elapsed_seconds = 0
        self._elapsed_timer.start(1000)
        self._open_mic()
        if self._screen_share_on and self._config.get("vision_api_key"):
            self._start_screen_capture()
        self._connecting_timer.start(CONNECTING_MS)

    def _enter_listening(self) -> None:
        self._set_phase("listening")
        self.subtitle.emit("聆听中，请说话")

    def hangup(self) -> None:
        """挂断：停管线 + ended。"""
        self._connecting_timer.stop()
        self._elapsed_timer.stop()
        self._close_mic()
        self._stop_screen_capture()
        self._tts.stop()
        self._set_phase("ended")
        self.subtitle.emit("通话结束")

    def toggle_mute(self) -> None:
        self._muted = not self._muted
        if self._stream is not None:
            try:
                # sounddevice stream 的 active 属性控制
                if self._muted:
                    self._stream.stop()
                else:
                    self._stream.start()
            except Exception:
                pass

    def toggle_screen_share(self) -> None:
        self._screen_share_on = not self._screen_share_on
        if self._screen_share_on and self._phase in ("connecting", "listening") \
                and self._config.get("vision_api_key"):
            self._start_screen_capture()
        elif not self._screen_share_on:
            self._stop_screen_capture()

    def _tick_elapsed(self) -> None:
        self._elapsed_seconds += 1
        self.elapsed.emit(self._elapsed_seconds)

    # ===== 麦克风 + VAD =====
    def _open_mic(self) -> None:
        try:
            import sounddevice as sd
            self._stream = sd.InputStream(
                samplerate=16000, channels=1, dtype="float32",
                blocksize=1024, callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as exc:
            self.error.emit(f"麦克风不可用：{exc}")
            self._set_phase("ended")

    def _close_mic(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """sounddevice 回调：每帧喂 VAD，检测到说话起止累积/提交。"""
        if self._vad_paused or self._muted:
            return
        samples = indata.flatten()
        result = self._vad.feed(samples)
        # 波形：RMS 归一化
        rms = result.rms
        self.waveform.emit(min(rms / self._vad.start_thresh, 1.0))
        if result.utterance_started:
            self._recording_buf = [samples.copy()]
            self.subtitle.emit("听到了，继续说…")
        elif self._vad.is_recording:
            self._recording_buf.append(samples.copy())
        if result.utterance_ended and self._recording_buf:
            audio = np.concatenate(self._recording_buf)
            self._recording_buf = []
            self._set_phase("processing")
            self.subtitle.emit("识别中…")
            # 转写 + LLM + TTS 在后台线程跑，避免阻塞音频回调
            threading.Thread(target=self._handle_utterance, args=(audio,), daemon=True).start()

    # ===== 管线：STT → 视觉附帧 → LLM → TTS =====
    def _handle_utterance(self, audio: np.ndarray) -> None:
        """处理一次"说完的话"：STT → 视觉附帧 → LLM → TTS。后台线程跑。"""
        try:
            wav_bytes = encode_wav(audio, 16000)
            text = self._transcribe(wav_bytes)
            if not text:
                self.subtitle.emit("没听清，再说一次？")
                self._set_phase("listening")
                return
            self.you_said.emit(text)

            # 屏幕附帧（spec §5.2）：取最新缓存帧给视觉模型
            screen_desc = ""
            if self._screen_share_on and self._config.get("vision_api_key"):
                frame = self._capturer.latest_frame
                if frame is not None:
                    screen_desc = describe_screen(
                        _frame_to_bytes(frame),
                        self._config.get("vision_endpoint") or self._config.get("endpoint", ""),
                        self._config["vision_api_key"],
                        self._config.get("vision_model", "gpt-4o"),
                    )

            user_msg = text
            if screen_desc:
                user_msg = f"{text}\n[当前屏幕: {screen_desc}]"
            self._last_user_message = user_msg
            self.subtitle.emit("思考中…")

            reply = self._stream_llm(user_msg)
            if not reply:
                self._set_phase("listening")
                self.subtitle.emit("聆听中，请说话")
                return

            # TTS：解析日语部分播放（与 desktop_pet 一致）
            parsed = parse_reply(reply)
            self._set_phase("speaking")
            self.subtitle.emit(f"{self._character.name} 正在说话…")
            tts_text = parsed.japanese or parsed.chinese
            if tts_text:
                self._play_tts(tts_text)
            else:
                # 无 TTS 内容，直接回 listening
                self._set_phase("listening")
                self.subtitle.emit("聆听中，请说话")
        except Exception as exc:
            self.error.emit(f"处理失败：{exc}")
            self._set_phase("listening")
            self.subtitle.emit("聆听中，请说话")

    def _transcribe(self, wav_bytes: bytes) -> str:
        return transcribe(
            wav_bytes,
            endpoint=self._config.get("asr_endpoint") or self._config.get("endpoint", ""),
            api_key=self._config.get("asr_api_key") or self._config.get("api_key", ""),
            model=self._config.get("asr_model", "mimo-audio-v1"),
        )

    def _stream_llm(self, user_text: str) -> str:
        """流式 LLM（复用 _stream_turn_direct，不带工具）。"""
        url = self._config["endpoint"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self._config['api_key']}"}
        system = self._soul_md + "\n\n" + KURISU_OUTPUT_FORMAT
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ]
        content, _ = _stream_turn_direct(
            url, headers, self._config["model"], messages,
            on_delta=lambda t: None,  # 电话模式不实时打字，TTS 分句时再驱动字幕
        )
        return content.strip()

    def _play_tts(self, text: str) -> None:
        """TTS 播放（SAPI 降级）。speaking_changed(False) 触发回 listening。"""
        self._tts.speak(text)

    def _on_tts_speaking_changed(self, speaking: bool) -> None:
        """TTS 播放结束 → 回 listening。"""
        if not speaking and self._phase == "speaking":
            self._set_phase("listening")
            self.subtitle.emit("聆听中，请说话")

    # ===== 屏幕截帧 =====
    def _start_screen_capture(self) -> None:
        self._capturer.start()

    def _stop_screen_capture(self) -> None:
        self._capturer.stop()


def _frame_to_bytes(frame) -> bytes:
    """mss 截帧对象 → bytes（BGRA 原始数据）。"""
    # mss 截帧是 ScreenShot 对象，有 .bgra 属性
    if hasattr(frame, "bgra"):
        return bytes(frame.bgra)
    if isinstance(frame, (bytes, bytearray)):
        return bytes(frame)
    return bytes(frame)