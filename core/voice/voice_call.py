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

from config import PHONE_DEFAULTS, VAD_PARAMS, get_character_by_id
from core.llm.agent_client import _load_soul_md
from core.voice.asr_client import encode_wav, transcribe
from core.emotion_parser import parse_reply
from core.vision.screen_capture import ScreenCapturer
from core.voice.vad import VADDetector
from core.vision.vision_client import describe_screen


CONNECTING_MS = 1300  # "正在接通"动画时长（移植原项目）


def _vlog(msg: str) -> None:
    """通话链路日志落地（data/voice_call.log）：只记状态转换级事件，
    用于定位「无声/无回复」类问题的现场（pythonw 无控制台，print 不可见）。
    测试进程不写（避免污染真实运行日志）。"""
    import os
    if "PYTEST_CURRENT_TEST" in os.environ:
        return
    try:
        from core.storage import APP_DIR
        with open(APP_DIR / "voice_call.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


class VoiceCallController(QObject):
    """电话模式控制器：状态机 + 管线编排。UI 通过信号驱动。"""

    phase_changed = Signal(str)       # idle/connecting/listening/processing/speaking/ended
    subtitle = Signal(str)            # 字幕文本
    you_said = Signal(str)            # 用户说的话
    waveform = Signal(float)          # 波形振幅 0-1
    elapsed = Signal(int)             # 通话秒数
    error = Signal(str)               # 错误提示
    screen_frame = Signal(object)     # 屏幕缩略图（给 UI 显示）
    muted_changed = Signal(bool)      # 静音状态
    screen_share_changed = Signal(bool)  # 屏幕共享状态
    mouth_intensity = Signal(float)   # TTS 播放音量，用于 Live2D 口型

    def __init__(self, config: dict, character=None, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._character = character or get_character_by_id("kurisu")
        self._phase = "idle"
        self._vad = VADDetector(params=VAD_PARAMS)
        self._capturer = ScreenCapturer(
            interval_ms=int(self._config.get(
                "capture_interval_ms",
                PHONE_DEFAULTS.get("capture_interval_ms", 2500),
            ))
        )
        self._stream = None            # sounddevice InputStream
        self._mic_sample_rate = 16000
        self._mic_peak = 0.0           # 死设备检测：开流以来最大 RMS
        self._mic_active_frames = 0    # 持续性判活：rms>0.0003 的帧数（瞬态冲击只有1-2帧，骗不过它）
        self._mic_rescanned = False    # 本通话是否已扫过设备（避免反复扫描）
        # 全双工：turn 计数（新语音/打断/挂断 +1，作废在途回复）与 barge-in 状态
        self._turn_id = 0
        self._active_turn = 0          # 当前 _handle_utterance 线程的 turn（delta 回调校验用）
        self._barge_frames = 0         # speaking 态连续超 barge 阈值的帧数
        self._barge_recording = False  # 打断已触发，正在录用户的话
        self._barge_silent = 0         # barge 录音中的连续静音帧
        self._barge_buf: list[np.ndarray] = []
        self._mic_ring: list[np.ndarray] = []  # 预滚环形（barge 触发时回补句首）
        self._recording_buf: list[np.ndarray] = []
        self._vad_paused = False       # 半双工：speaking/processing 态暂停 VAD
        self._muted = False
        self._screen_share_on = bool(self._config.get(
            "screen_share_default",
            PHONE_DEFAULTS.get("screen_share_default", True),
        ))
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
        from core.voice.tts_client import SpeechPlayer
        self._tts = SpeechPlayer(self)
        self._tts.speaking_changed.connect(self._on_tts_speaking_changed)
        self._tts.mouth_intensity.connect(self.mouth_intensity.emit)
        self._tts.tts_offline.connect(self._on_tts_offline)
        self._tts.tts_error.connect(self._on_tts_error)
        self._tts.tts_degraded.connect(self._on_tts_degraded)
        # 软件 AEC（core/aec.py）：TTS 播放 PCM push 到远端参考，
        # 麦克风信号消除回声后喂 VAD —— 她说话时也能检测用户插话（真全双工）。
        # 未启用/未收敛时 speaking 态回退 barge-in 电平门槛路径。
        from config import AEC_PARAMS
        from core.voice.aec import AECFilter, EchoReference
        self._aec_cfg = {**AEC_PARAMS, **(self._config.get("aec") or {})}
        self._aec = AECFilter(
            filter_len_ms=float(self._aec_cfg["filter_len_ms"]),
            mu=float(self._aec_cfg["mu"]),
            align_delay_ms=float(self._aec_cfg["align_delay_ms"]),
            nlp_threshold=float(self._aec_cfg["nlp_threshold"]),
            nlp_gain=float(self._aec_cfg["nlp_gain"]),
            convergence_ms=float(self._aec_cfg["convergence_ms"]),
        ) if self._aec_cfg.get("enabled", True) else None
        self._echo_ref = EchoReference() if self._aec is not None else None
        self._tts.echo_ref = self._echo_ref
        # 流式 LLM/TTS 状态：=== 计数器奇偶判断（与 desktop_pet._agent_delta 对齐）
        # _stream_sep_count 记录已遇 === 数（奇数=日语段，偶数=中文段）
        # _stream_tts_started 标记是否已启动 speak_streaming_start
        self._stream_sep_count = 0
        self._stream_tts_started = False
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
        # 全双工：只有 speaking 暂停主 VAD（她的声音会回流麦克风；用户开口
        # 走 barge-in 高门槛检测，见 _audio_callback）。processing（STT/LLM
        # 网络等待期，扬声器无声）放开 VAD —— 用户可以直接改口说下一句，
        # 新语音自动作废在途的旧回复（_turn_id 机制）
        self._vad_paused = phase == "speaking"
        if phase in ("speaking", "processing", "ended"):
            self._vad.reset()
        self.phase_changed.emit(phase)

    def start(self) -> None:
        """启动通话：connecting → (1.3s) → listening。"""
        _vlog("call start")
        self._set_phase("connecting")
        self.subtitle.emit("正在接通…")
        self._elapsed_seconds = 0
        self.elapsed.emit(0)
        self.muted_changed.emit(self._muted)
        self.screen_share_changed.emit(self._screen_share_on)
        self._elapsed_timer.start(1000)
        self._open_mic()
        if self._phase == "ended":
            self._elapsed_timer.stop()
            return
        if self._screen_share_on and self._config.get("vision_api_key"):
            self._start_screen_capture()
        self._connecting_timer.start(CONNECTING_MS)

    def _enter_listening(self) -> None:
        self._set_phase("listening")
        self.subtitle.emit("聆听中，请说话")

    def hangup(self) -> None:
        """挂断：停管线 + ended。"""
        _vlog(f"hangup phase={self._phase} tts_started={self._stream_tts_started}")
        self._turn_id += 1  # 作废在途回合（LLM 线程回来后直接丢弃）
        self._barge_recording = False
        self._barge_buf = []
        self._barge_frames = 0
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
        if self._muted:
            self._vad.reset()
            self._recording_buf = []
            self.waveform.emit(0.0)
            self.subtitle.emit("麦克风已静音")
        elif self._phase in ("connecting", "listening"):
            self.subtitle.emit("聆听中，请说话")
        self.muted_changed.emit(self._muted)

    def toggle_screen_share(self) -> None:
        self._screen_share_on = not self._screen_share_on
        if self._screen_share_on and self._phase in ("connecting", "listening") \
                and self._config.get("vision_api_key"):
            self._start_screen_capture()
        elif not self._screen_share_on:
            self._stop_screen_capture()
        self.screen_share_changed.emit(self._screen_share_on)
        if self._screen_share_on and not self._config.get("vision_api_key"):
            self.subtitle.emit("屏幕共享已开启（未配置视觉模型，仅语音通话）")
        elif self._phase in ("connecting", "listening"):
            self.subtitle.emit("屏幕共享已开启" if self._screen_share_on else "屏幕共享已关闭")

    def _tick_elapsed(self) -> None:
        self._elapsed_seconds += 1
        self.elapsed.emit(self._elapsed_seconds)

    # ===== 麦克风 + VAD =====
    def _open_mic(self) -> None:
        try:
            import sounddevice as sd
            # 显式配置的设备优先（phone.mic_device_index）
            configured = self._config.get("mic_device_index")
            default_in = int(configured) if isinstance(configured, int) and configured >= 0 else sd.default.device[0]
            self._mic_peak = 0.0
            self._mic_active_frames = 0
            self._mic_rescanned = False
            if not self._open_mic_device(default_in):
                raise RuntimeError(f"无法打开输入设备 {default_in}")
            # 死设备检测（两次）：1.5s 首检 + 16s 兜底复查。实测空置的
            # Realtek 插孔 RMS 0.00002，但开流瞬态冲击可达 0.003（1-2 帧），
            # 会骗过"峰值判活"——所以用持续性判活（rms>0.0003 的帧数 ≥3），
            # 并在 16s 再查一次以防首检漏网。浏览器 getUserMedia 拿 Windows
            # 默认通信设备（用户实际的耳机/阵列麦），PortAudio default 常拿到
            # 不同的（死）设备 —— 这里补齐这个差异。
            QTimer.singleShot(1500, self._check_mic_alive)
            QTimer.singleShot(16000, self._check_mic_alive)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.error.emit(f"麦克风不可用：{exc}")
            self._set_phase("ended")

    def _open_mic_device(self, device_index: int) -> bool:
        """在指定输入设备上开麦克风流。成功返回 True。"""
        try:
            import sounddevice as sd
            try:
                sd.check_input_settings(device=device_index, samplerate=16000, channels=1)
                sr = 16000
            except sd.PortAudioError:
                dev_info = sd.query_devices(device_index)
                sr = int(dev_info["default_samplerate"])
                _vlog(f"mic device {device_index} 不支持 16000Hz，用 {sr}Hz")
            self._stream = sd.InputStream(
                samplerate=sr, channels=1, dtype="float32",
                blocksize=1024, callback=self._audio_callback,
                device=device_index,
            )
            self._mic_sample_rate = sr
            self._mic_device_index = device_index
            self._stream.start()
            _vlog(f"mic started device={device_index} sr={sr}")
            return True
        except Exception as exc:
            _vlog(f"mic device {device_index} open failed: {exc}")
            return False

    def _check_mic_alive(self) -> None:
        """当前设备是否在持续拾音：不是则后台扫描切换（见 _open_mic 注释）。

        持续性判活：rms > 0.0003 的帧数 ≥3 才算活。开流瞬态冲击只有
        1-2 帧（实测死插孔曾以 0.00379 的瞬态峰值骗过"峰值判活"导致
        整通通话波形死平）；真设备至少有持续电噪（0.0001+/帧）。
        """
        if self._stream is None or self._phase not in ("connecting", "listening"):
            return
        if self._mic_active_frames >= 3:
            _vlog(f"mic alive, peak={self._mic_peak:.5f} active_frames={self._mic_active_frames}")
            return
        if self._mic_rescanned:
            _vlog(f"mic still dead after rescan (peak={self._mic_peak:.5f} active={self._mic_active_frames})")
            return
        _vlog(f"mic appears dead (peak={self._mic_peak:.5f} active_frames={self._mic_active_frames}), rescanning…")
        self._mic_rescanned = True
        threading.Thread(target=self._rescan_mic_device, daemon=True).start()

    def _rescan_mic_device(self) -> None:
        """后台并行探测其他输入设备（各 0.8s），只在找到明显更强的设备时切换。

        关键：探测期间【不关当前流】——多设备可并存，旧流保持工作，
        波形/收音不中断；只有确认切换才原子地关旧开新。
        """
        try:
            import sounddevice as sd
            current = getattr(self, "_mic_device_index", None)
            best_idx, best_peak = None, 0.0
            for idx, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] <= 0 or idx == current:
                    continue
                peak = self._probe_input_device(sd, idx)
                _vlog(f"probe device[{idx}] {str(d['name'])[:30]!r} peak={peak:.5f}")
                if peak > best_peak:
                    best_idx, best_peak = idx, peak
            # 切换条件：当前设备死（持续性判活未通过）时，活设备哪怕只有
            # 持续电噪（0.0001+，死插孔是 0.00002）也值得切——安静环境下
            # 这是唯一能区分死活的信号；设备活着时按"明显更好"（5 倍+）
            # 标准，避免在都正常的设备间来回横跳
            current_dead = self._mic_active_frames < 3
            if current_dead:
                should_switch = best_idx is not None and best_peak > 0.0001
            else:
                should_switch = best_idx is not None and best_peak >= max(0.001, self._mic_peak * 5)
            if not should_switch:
                _vlog(f"no better device (best={best_peak:.5f} current_peak={self._mic_peak:.5f} current_dead={current_dead})")
                return
            if self._phase not in ("connecting", "listening"):
                return  # 通话已进入后续阶段，不再动设备
            _vlog(f"switching mic to device[{best_idx}] peak={best_peak:.5f}")
            self._close_mic()
            self._mic_peak = 0.0
            self._mic_active_frames = 0
            self._open_mic_device(best_idx)
        except Exception as exc:
            _vlog(f"mic rescan failed: {exc}")

    @staticmethod
    def _probe_input_device(sd, device_index: int, duration: float = 0.8) -> float:
        """探测某输入设备的信号峰值（短开短关）。"""
        peaks: list[float] = []

        def _cb(indata, frames, time_info, status) -> None:
            peaks.append(VADDetector.compute_rms(indata.flatten()))

        try:
            with sd.InputStream(
                samplerate=16000, channels=1, dtype="float32",
                blocksize=1024, callback=_cb, device=device_index,
            ):
                time.sleep(duration)
        except Exception:
            return -1.0
        return max(peaks) if peaks else 0.0

    def _close_mic(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """sounddevice 回调：每帧喂 VAD，检测到说话起止累积/提交。

        全双工分相处理：
        - listening/processing：正常 VAD（processing 期扬声器无声，用户可
          直接改口，新语音自动作废在途旧回复）
        - speaking：主 VAD 暂停（她的话会回流麦克风），改用 barge-in 高
          门槛检测 —— 用户真实开口（电平 ≥ 自适应阈值×2.5，连续 2 帧）
          立即停 TTS 转听用户，TTS 回流电平低触发不了
        """
        if self._muted:
            return
        samples = indata.flatten()
        # 波形始终发射（即使 VAD 暂停），让用户看到麦克风在接收；
        # 除数用当前自适应阈值，弱信号麦克风下波形也能满幅显示
        rms = VADDetector.compute_rms(samples)
        self._mic_peak = max(self._mic_peak, rms)
        # 持续性判活计数：真设备每帧至少有电噪（>0.0003），
        # 死设备只有开流瞬态的 1-2 帧（见 _check_mic_alive）
        if rms > 0.0003:
            self._mic_active_frames += 1
        self.waveform.emit(min(rms / self._vad.current_start_thresh, 1.0))
        # 预滚环形：barge 触发时回补句首（~0.4s）
        self._mic_ring.append(samples.copy())
        if len(self._mic_ring) > 6:
            self._mic_ring.pop(0)

        if self._barge_recording:
            self._feed_barge_record(samples, rms)
            return
        # AEC 全双工：她说话时用回声消除后的信号喂主 VAD —— 用户插话直接
        # 触发正常 utterance 流程（录音送 STT 的也是干净信号）。
        # 未启用/未收敛/参考不新鲜时回退 barge-in 电平门槛路径
        vad_input = samples
        if self._phase == "speaking":
            cleaned = self._aec_process(samples)
            if cleaned is not None:
                vad_input = cleaned
            else:
                self._feed_barge_in(samples, rms)
                return
        if self._vad_paused and self._phase != "speaking":
            return
        result = self._vad.feed(vad_input)
        if result.utterance_started:
            self._recording_buf = [vad_input.copy()]
            self.subtitle.emit("听到了，继续说…")
            _vlog("VAD utterance start")
        elif self._vad.is_recording:
            self._recording_buf.append(vad_input.copy())
        if result.utterance_ended and self._recording_buf:
            audio = np.concatenate(self._recording_buf)
            self._recording_buf = []
            _vlog(f"VAD utterance end, {audio.size / self._mic_sample_rate:.1f}s audio")
            self._submit_user_audio(audio)

    def _aec_process(self, samples: np.ndarray) -> np.ndarray | None:
        """speaking 态回声消除。返回干净信号；不可用（AEC 关/未收敛/
        参考缓冲不新鲜/参考不足）返回 None，调用方回退 barge-in。"""
        if self._aec is None or self._echo_ref is None:
            return None
        if not self._echo_ref.playing():
            return None  # 没在播（尾部残余 <0.3s）：无回声，但此时不在 speaking 态，不会到这
        need = samples.size + self._aec.filter_len - 1
        hist = self._echo_ref.window(need, end_delay_samples=self._aec.align_delay)
        if hist is None:
            return None
        if not self._aec.converged:
            # 收敛期也要喂样本让滤波器学习（她的话=训练数据），但不用于 VAD
            self._aec.process(samples, hist)
            return None
        return self._aec.process(samples, hist)

    def _feed_barge_in(self, samples: np.ndarray, rms: float) -> None:
        """speaking 态用户打断检测：电平 ≥ 自适应阈值×2.5 连续 2 帧 → 打断。

        阈值是正常触发门槛的 2.5 倍：她的声音经 扬声器→空气→麦克风 衰减后
        电平低（无 AEC 场景的回声过滤），只有用户真开口（对着麦克风说话）
        才够得着；触发即停 TTS（她立刻闭嘴），转入 _feed_barge_record 录完
        用户这句话再提交。
        """
        barge_thresh = self._vad.current_start_thresh * 2.5
        if rms > barge_thresh:
            self._barge_frames += 1
            if self._barge_frames == 1:
                self._barge_buf = list(self._mic_ring)  # 预滚回补句首
            self._barge_buf.append(samples.copy())
            if self._barge_frames >= 2:
                self._barge_frames = 0
                self._barge_recording = True
                self._barge_silent = 0
                _vlog("barge-in triggered, stopping TTS")
                self._tts.stop()  # speaking_changed(False) → listening，VAD 已被 barge 录音接管
        else:
            self._barge_frames = 0
            self._barge_buf = []

    def _feed_barge_record(self, samples: np.ndarray, rms: float) -> None:
        """打断后的用户语句录音：录到连续 ~0.77s 低于正常阈值即说完提交。"""
        self._barge_buf.append(samples.copy())
        if rms < self._vad.current_start_thresh:
            self._barge_silent += 1
        else:
            self._barge_silent = 0
        if self._barge_silent >= 12:
            audio = np.concatenate(self._barge_buf)
            self._barge_recording = False
            self._barge_buf = []
            _vlog(f"barge-in speech done, {audio.size / self._mic_sample_rate:.1f}s")
            self._submit_user_audio(audio)

    def _submit_user_audio(self, audio: np.ndarray) -> None:
        """提交一段用户语音开新回合：作废在途回复（改口/打断场景）。"""
        self._turn_id += 1
        # 在途回合可能已开始播 TTS（barge-in 由调用方 stop；改口场景在此兜底停）
        self._tts.stop()
        self._set_phase("processing")
        self.subtitle.emit("识别中…")
        threading.Thread(
            target=self._handle_utterance, args=(audio, self._turn_id), daemon=True
        ).start()

    # ===== 管线：STT → 视觉附帧 → LLM → 流式 TTS =====
    def _handle_utterance(self, audio: np.ndarray, turn_id: int | None = None) -> None:
        """处理一次"说完的话"：STT → 视觉附帧 → 流式 LLM + 流式 TTS。后台线程跑。

        turn_id：全双工回合号。新语音/打断/挂断会 _turn_id += 1 作废在途回合
        （改口场景旧回复不再播放、旧回合不再改状态），各关键点校验。

        流式 TTS 触发流程（与 desktop_pet._agent_delta/_agent_finished 对齐）：
        1. _stream_llm 启动流式 LLM，on_delta 回调到 _on_llm_delta
        2. _on_llm_delta 检测 === 分隔符，首次出现时启动 speak_streaming_start，
           之后增量追加日语 delta 到 TTS 合成队列
        3. _stream_llm 返回后：
           - 若 _stream_japanese_started=True，调 speak_streaming_end 刷新剩余缓冲，
             speaking_changed(False) → _on_tts_speaking_changed → 回 listening
           - 否则兜底整段合成（用 parse_reply 提取日语/中文）
        """
        if turn_id is None:
            turn_id = self._turn_id
        try:
            # 软件增益（替代浏览器 autoGainControl）：无 AGC 的原始麦克风信号弱
            # （实测说话 RMS ~0.005），归一化到峰值 0.25 提升 ASR 识别率
            if isinstance(audio, np.ndarray) and audio.size:
                peak = float(np.max(np.abs(audio)))
                if 0.0 < peak < 0.25:
                    audio = (audio / peak * 0.25).astype(np.float32)
            wav_bytes = encode_wav(audio, self._mic_sample_rate)
            text = self._transcribe(wav_bytes)
            if turn_id != self._turn_id:
                _vlog("turn stale after STT, drop")
                return
            if not text:
                _vlog("STT empty result")
                self.subtitle.emit("没听清，再说一次？")
                self._set_phase("listening")
                return
            _vlog(f">>> 语音识别结果: {text[:200]!r}")
            self.you_said.emit(text)
            # 识别结果立即上字幕（"思考中…"之前让用户看见"她听到了什么"）
            self.subtitle.emit(f"🎤 你：{text}")

            # 屏幕附帧（spec §5.2）：取最新缓存帧给视觉模型
            screen_desc = ""
            if self._screen_share_on and self._config.get("vision_api_key"):
                frame = self._capturer.latest_frame
                if frame is not None:
                    screen_desc = describe_screen(
                        frame,
                        self._config.get("vision_endpoint") or self._config.get("endpoint", ""),
                        self._config["vision_api_key"],
                        self._config.get("vision_model", "gpt-4o"),
                    )

            user_msg = text
            if screen_desc:
                user_msg = f"{text}\n[当前屏幕: {screen_desc}]"
            self._last_user_message = user_msg
            self.subtitle.emit("思考中…")

            # 重置流式 TTS 状态：与 desktop_pet._agent_delta 的 === 计数器对齐
            self._stream_sep_count = 0
            self._stream_tts_started = False
            self._streamed_reply = ""
            self._active_turn = turn_id  # delta 回调校验（旧回合 delta 丢弃）

            _vlog("LLM stream begin")
            reply = self._stream_llm(user_msg)
            if turn_id != self._turn_id:
                _vlog(f"turn stale after LLM (turn={turn_id} cur={self._turn_id}), drop reply")
                return
            _vlog(f"LLM done, reply_len={len(reply)} tts_started={self._stream_tts_started}")
            if not reply:
                self._set_phase("listening")
                self.subtitle.emit("聆听中，请说话")
                return

            # TTS 收尾
            if self._stream_tts_started:
                # 流式 TTS 会话结束：刷新剩余缓冲，speaking_changed(False) → 回 listening
                _vlog("TTS speak_streaming_end")
                parsed = parse_reply(reply)
                fallback_text = parsed.chinese or parsed.japanese
                fallback_lang = "zh" if parsed.chinese else "ja"
                self._tts.speak_streaming_end(
                    fallback_text=fallback_text,
                    fallback_lang=fallback_lang,
                )
            else:
                # 兜底：LLM 没输出 === 分隔符（异常），整段合成。
                # 语音只出日语（需求）；无日语段的纯中文回复用中文腔读
                # （比日语音读中文自然），不混语言
                parsed = parse_reply(reply)
                tts_text = parsed.japanese or parsed.chinese
                text_lang = "ja" if parsed.japanese else "zh"
                _vlog(f"TTS fallback speak, jp_len={len(parsed.japanese)} cn_len={len(parsed.chinese)} lang={text_lang}")
                if tts_text:
                    self._set_phase("speaking")
                    self._streamed_reply = reply
                    self._emit_reply_subtitle()
                    self._tts.speak_with_options(
                        tts_text,
                        text_lang=text_lang,
                        allow_fallback=True,
                        fallback_text=parsed.chinese or tts_text,
                        fallback_lang="zh" if parsed.chinese else "ja",
                    )
                else:
                    # 无 TTS 内容，直接回 listening
                    self._set_phase("listening")
                    self.subtitle.emit("聆听中，请说话")
        except Exception as exc:
            _vlog(f"FAIL: {type(exc).__name__}: {exc}")
            if turn_id != self._turn_id:
                return  # 旧回合的异常不覆盖新回合状态
            self.error.emit(f"处理失败：{exc}")
            # 停止可能悬挂的流式 TTS（已启动 speak_streaming_start 但中途失败）
            if self._stream_tts_started:
                self._tts.stop()
            self._set_phase("listening")
            self.subtitle.emit("聆听中，请说话")

    def _transcribe(self, wav_bytes: bytes) -> str:
        return transcribe(
            wav_bytes,
            endpoint=self._config.get("asr_endpoint") or PHONE_DEFAULTS["asr_endpoint"],
            api_key=self._config.get("asr_api_key") or PHONE_DEFAULTS["asr_api_key"],
            model=self._config.get("asr_model") or PHONE_DEFAULTS["asr_model"],
        )

    def _stream_llm(self, user_text: str) -> str:
        """通过统一路由调用 harness / agent 后端。

        这条路径会沿用桌面端同一套路由、审批、工具和流式 delta 处理，
        让电话模式不再绕开 harness 内核。
        """
        from core.llm.backend_router import route_and_send
        from core.companion.call_style import build_phone_short_reply_prompt
        reply, _backend = route_and_send(
            config=self._config,
            input_text=user_text,
            soul_md=self._soul_md,
            conversation_history=None,
            memories=None,
            on_delta=self._on_llm_delta,
            on_status=self.subtitle.emit,
            on_approval=lambda _: "deny",
            system_role="user",
            skip_history=True,
            inject_system_prompt=build_phone_short_reply_prompt(),
            # 不设 max_tokens：推理模型（mimo-v2.5 等）思考即耗 token，
            # 上限会把正文整段掐掉 → 空回复；长度交给 phone prompt 的 1-2 句约束
            response_max_tokens=None,
        )
        return reply.strip()

    def _on_llm_delta(self, delta: str) -> None:
        """LLM 流式 delta 回调：纯 === 切段 + 日语字符过滤（与 desktop_pet._agent_delta 对齐）。

        修复 bug：LLM 把 [emotion:xxx] 放在 === 后的日语段里（违反 prompt 约定），
        双重切段逻辑会错误把日语段1重置为中文段，后续 TTS 全跳过 → 无声。
        新方案：纯 === 切段，对每段用 has_japanese() 判断含假名则送 TTS。

        物理意义：LLM 生成与 TTS 合成是 producer-consumer 关系。LLM 生成第一句日语
        时立即启动 TTS，TTS 合成线程与 LLM 生成线程并行。相比"等 LLM 全部完成再合成"，
        首句语音延迟从「LLM 总时长 + TTS 合成」降到「LLM 首句时长 + TTS 合成」。
        """
        if not delta:
            return
        # 全双工回合校验：旧回合（被打断/已改口）的 delta 不送 TTS、不上字幕
        if self._active_turn != self._turn_id:
            return
        self._streamed_reply += delta
        # 把 delta 按 === 切分：parts[0] 是当前段尾部，parts[1:] 是新切段开头
        parts = delta.split("===")
        if len(parts) == 1:
            # 无新 ===：当前段增量追加（按假名判断是否送 TTS）
            self._append_tts_segment_by_japanese(parts[0])
        else:
            # 有新 ===：先处理当前段尾部，再逐个切段
            self._append_tts_segment_by_japanese(parts[0])
            for i in range(1, len(parts)):
                seg = parts[i].lstrip("=\r\n")
                self._append_tts_segment_by_japanese(seg)
        # 实时字幕：让用户看到她「正在说什么」。修复通话中回复从不显示、
        # 用户在漫长首声等待中误以为死机而挂断的问题。
        self._emit_reply_subtitle()

    def _emit_reply_subtitle(self) -> None:
        """把当前流式回复清理后作为字幕显示：只出中文（需求），纯日语输出
        （无翻译段）时回退显示日语原文，避免空白。

        parse_reply 按假名分类段落、顺序无关：无论 LLM 输出日语在前还是
        中文在前，都能正确分列。字幕随 delta 增量刷新。
        """
        parsed = parse_reply(self._streamed_reply)
        text = parsed.chinese or parsed.japanese
        if text:
            self.subtitle.emit(text)

    def _append_tts_segment_by_japanese(self, text: str) -> None:
        """按假名判断是否追加 TTS：含假名的是日语段，提取假名段送 TTS。

        LLM 把日语 + 中文混在同一段（如「日语1 \\n\\n [emotion:neutral]中文2」），
        用策略：先按 [emotion:xxx] 标签切分（去掉标签），再按空白行切段，
        对每段用 has_japanese() 判断含假名则送 TTS，跳过纯中文段。

        物理意义：日语必有假名（U+3040-309F 平假名 + U+30A0-30FF 片假名），
        CJK 汉字无法区分但日语段必含假名。
        形象理解：把文本按空白行切块，每块扫假名，有假名的是日语块送 TTS。
        """
        if not text:
            return
        import re
        # 去掉 [emotion:xxx] 标签
        cleaned = re.sub(r"\[emotion:[^\]]+\]", "", text)
        # 按空白行（\\n\\n 或 \\n）切段
        chunks = re.split(r"\n\s*\n", cleaned)
        for chunk in chunks:
            segment = chunk.strip()
            if not segment:
                continue
            # 必须含至少 1 个假名才算日语段
            if not re.search(r"[\u3040-\u309F\u30A0-\u30FF]", segment):
                continue
            # 首次进入日语段时启动流式 TTS 会话
            if not self._stream_tts_started:
                _vlog("TTS streaming start (first kana segment)")
                # first_merge_chars=1：电话模式首句切出句末标点即送合成，
                # 不等 14 字合并（延迟优先于吞吐，缩短首声等待）
                self._tts.speak_streaming_start(
                    text_lang="ja",
                    first_merge_chars=1,
                    allow_fallback=True,
                )
                self._stream_tts_started = True
                # phase 切到 speaking（VAD 保持暂停，半双工）
                self._set_phase("speaking")
                self._emit_reply_subtitle()
            self._tts.speak_streaming_append(segment)

    def _on_tts_speaking_changed(self, speaking: bool) -> None:
        """TTS 播放状态变化：speaking=False 时回 listening（半双工恢复）。"""
        _vlog(f"TTS speaking_changed={speaking} phase={self._phase}")
        if not speaking and self._phase == "speaking":
            self._set_phase("listening")
            self.subtitle.emit("聆听中，请说话")

    def _on_tts_offline(self) -> None:
        """TTS 服务不可用时给通话 UI 明确反馈，并恢复聆听态。"""
        _vlog("TTS OFFLINE: provider unavailable")
        self.error.emit("语音合成服务不可用，请检查 TTS Provider / API Key / 音色 ID")
        if self._phase in ("speaking", "processing"):
            self._set_phase("listening")
        self.subtitle.emit("语音合成服务不可用，请检查语音设置")

    def _on_tts_error(self, message: str) -> None:
        _vlog(f"TTS ERROR: {message}")
        self.error.emit(f"语音输出失败：{message}")

    def _on_tts_degraded(self, message: str) -> None:
        _vlog(f"TTS DEGRADED: {message}")
        self.subtitle.emit("云端音色不可用，已切换 Windows 系统语音")

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
