"""VoiceCallController：电话模式状态机 + 语音管线编排。

移植原项目 amadeus/src/components/VoiceCall.tsx 的 VAD + 回合制 STT + 状态机，
新增 mss 截帧 + GPT-4o 视觉的屏幕共享旁路。

状态机：connecting → listening → processing → speaking → listening(循环) → ended
半双工：speaking/processing 态暂停 VAD（移植 speakingRef），避免她的声音从麦克风
        回流被误判为用户说话。
屏幕附帧：utterance end 时取最新缓存帧给视觉模型，频率=说话频率（省钱）。

流式 TTS（与 amadeus src/lib/tts.ts + src/components/VoiceCall.tsx 对齐）：
- LLM 流式输出 → on_delta 回调检测 === 分隔符，日语段进入 speak_streaming_start
- speak_streaming_append 增量推送日语 delta 到 TTS 合成队列
- speak_streaming_end 在 LLM 完成时刷新剩余缓冲
- speaking_changed(False) → 回 listening（半双工恢复）
- 兜底：LLM 无 === 分隔符时整段 speak_with_options 合成

数学本质：LLM 生成与 TTS 合成是 producer-consumer 关系，用 SpeechPlayer 内部
双缓冲队列解耦（_stream_queue 连接 LLM→合成，_playback_queue 连接合成→播放）。
总时延 ≈ max(LLM 生成, TTS 合成, 音频播放)，相比串行省 min(LLM, TTS)。
形象理解：LLM 是"做菜师傅"，TTS 是"传菜员"，师傅不必等传菜员传完才做下一道菜。
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
        # TTS：流式 SpeechPlayer，默认走 aliyun CosyVoice（与普通对话对齐），
        # SAPI 仅作 allow_fallback 兜底
        from core.tts_client import SpeechPlayer
        self._tts = SpeechPlayer(self)
        self._tts.speaking_changed.connect(self._on_tts_speaking_changed)
        # 流式 LLM/TTS 状态：照搬 desktop_pet._agent_delta 的 === 分隔符检测
        self._stream_japanese_started = False
        self._streamed_reply = ""

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
            # 诊断：列出可用设备
            devices = sd.query_devices()
            default_in = sd.default.device[0]
            print(f"[VoiceCall] 默认输入设备: {default_in}")
            print(f"[VoiceCall] 可用设备: {len(devices)} 个")
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0:
                    print(f"  [{i}] {d['name']} (in={d['max_input_channels']}ch, "
                          f"sr={d['default_samplerate']:.0f})")
            # 尝试 16000 Hz，失败则用设备默认采样率
            try:
                sd.check_input_settings(device=default_in, samplerate=16000, channels=1)
                sr = 16000
            except sd.PortAudioError:
                dev_info = sd.query_devices(default_in)
                sr = int(dev_info["default_samplerate"])
                print(f"[VoiceCall] 设备不支持 16000Hz，使用 {sr}Hz")
            self._stream = sd.InputStream(
                samplerate=sr, channels=1, dtype="float32",
                blocksize=1024, callback=self._audio_callback,
                device=default_in,
            )
            self._stream.start()
            print(f"[VoiceCall] 麦克风已启动 sr={sr} device={default_in}")
        except Exception as exc:
            import traceback
            traceback.print_exc()
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
        if self._muted:
            return
        samples = indata.flatten()
        # 波形始终发射（即使 VAD 暂停），让用户看到麦克风在接收
        rms = VADDetector.compute_rms(samples)
        self.waveform.emit(min(rms / self._vad.start_thresh, 1.0))
        if self._vad_paused:
            return
        result = self._vad.feed(samples)
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

    # ===== 管线：STT → 视觉附帧 → LLM → 流式 TTS =====
    def _handle_utterance(self, audio: np.ndarray) -> None:
        """处理一次"说完的话"：STT → 视觉附帧 → 流式 LLM + 流式 TTS。后台线程跑。

        流式 TTS 触发流程（与 desktop_pet._agent_delta/_agent_finished 对齐）：
        1. _stream_llm 启动流式 LLM，on_delta 回调到 _on_llm_delta
        2. _on_llm_delta 检测 === 分隔符，首次出现时启动 speak_streaming_start，
           之后增量追加日语 delta 到 TTS 合成队列
        3. _stream_llm 返回后：
           - 若 _stream_japanese_started=True，调 speak_streaming_end 刷新剩余缓冲，
             speaking_changed(False) → _on_tts_speaking_changed → 回 listening
           - 否则兜底整段合成（用 parse_reply 提取日语/中文）
        """
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

            # 重置流式 TTS 状态：照搬 desktop_pet._agent_delta 的 === 检测
            self._stream_japanese_started = False
            self._streamed_reply = ""

            reply = self._stream_llm(user_msg)
            if not reply:
                self._set_phase("listening")
                self.subtitle.emit("聆听中，请说话")
                return

            # TTS 收尾
            if self._stream_japanese_started:
                # 流式 TTS 会话结束：刷新剩余缓冲，speaking_changed(False) → 回 listening
                self._tts.speak_streaming_end()
            else:
                # 兜底：LLM 没输出 === 分隔符（异常），整段合成日语/中文部分
                parsed = parse_reply(reply)
                tts_text = parsed.japanese or parsed.chinese
                if tts_text:
                    self._set_phase("speaking")
                    self.subtitle.emit(f"{self._character.name} 正在说话…")
                    self._tts.speak_with_options(
                        tts_text,
                        text_lang="ja",
                        allow_fallback=False,
                    )
                else:
                    # 无 TTS 内容，直接回 listening
                    self._set_phase("listening")
                    self.subtitle.emit("聆听中，请说话")
        except Exception as exc:
            self.error.emit(f"处理失败：{exc}")
            # 停止可能悬挂的流式 TTS（已启动 speak_streaming_start 但中途失败）
            if self._stream_japanese_started:
                self._tts.stop()
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
        """流式 LLM（复用 _stream_turn_direct，不带工具）。

        on_delta 回调到 _on_llm_delta，照搬 desktop_pet._agent_delta 的 === 检测逻辑：
        检测到中日分隔符 === 后启动流式 TTS，只把日语段追加到 TTS 合成队列，
        中文段不送 TTS（与 desktop_pet 普通对话完全对齐）。
        """
        url = self._config["endpoint"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self._config['api_key']}"}
        system = self._soul_md + "\n\n" + KURISU_OUTPUT_FORMAT
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ]
        content, _ = _stream_turn_direct(
            url, headers, self._config["model"], messages,
            on_delta=self._on_llm_delta,
        )
        return content.strip()

    def _on_llm_delta(self, delta: str) -> None:
        """LLM 流式 delta 回调：照搬 desktop_pet._agent_delta 的流式 TTS 触发。

        与 desktop_pet.py:1232-1250 完全对齐：
        1. 累积 delta 到 _streamed_reply
        2. 检测首次 === 分隔符：
           - 启动 speak_streaming_start 会话
           - 切换 phase 到 speaking（VAD 保持暂停，避免麦克风拾取自身语音）
           - 提取 === 之后的日语部分追加到 TTS
        3. 后续 delta 已在日语段，去掉可能残留的 === 后增量追加

        物理意义：LLM 生成与 TTS 合成是 producer-consumer 关系。LLM 生成第一句日语
        时立即启动 TTS，TTS 合成线程与 LLM 生成线程并行。相比"等 LLM 全部完成再合成"，
        首句语音延迟从「LLM 总时长 + TTS 合成」降到「LLM 首句时长 + TTS 合成」。
        """
        if not delta:
            return
        self._streamed_reply += delta
        if not self._stream_japanese_started:
            if "===" in self._streamed_reply:
                self._stream_japanese_started = True
                # 启动流式 TTS 会话（默认 text_lang="ja"）
                self._tts.speak_streaming_start(text_lang="ja")
                # phase 切到 speaking（VAD 保持暂停，半双工）
                self._set_phase("speaking")
                self.subtitle.emit(f"{self._character.name} 正在说话…")
                # 提取 === 之后的日语部分追加
                jp_part = self._streamed_reply.split("===", 1)[1].lstrip("=\r\n").strip()
                if jp_part:
                    self._tts.speak_streaming_append(jp_part)
        else:
            # 已在日语段，增量追加（去掉可能残留的 ===）
            t = delta
            if "===" in t:
                t = t.split("===", 1)[-1].lstrip("=\r\n")
            if t:
                self._tts.speak_streaming_append(t)

    def _on_tts_speaking_changed(self, speaking: bool) -> None:
        """TTS 播放状态变化：speaking=False 时回 listening（半双工恢复）。"""
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