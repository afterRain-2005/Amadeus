# core/vad.py
"""VAD（Voice Activity Detection）：RMS 振幅阈值滞回状态机 + 底噪自适应。

移植原项目 amadeus/src/components/VoiceCall.tsx:23-27 的参数与逻辑，
新增底噪自适应阈值（软件替代浏览器 autoGainControl）。

为什么需要自适应：原 TS 项目走浏览器 getUserMedia
（autoGainControl: true），信号被自动增益归一化，说话 RMS 通常 0.05-0.3，
固定阈值 0.018 轻松超过。Python 版 sounddevice 裸 PortAudio 无 AGC，
原始电平常仅 0.002-0.01（实测：说话 0.005 量级、死插孔 0.00002），
固定 0.018 永不触发 → 通话「无声」。阈值随底噪浮动：
start_thresh = clamp(noise_floor × noise_ratio, min_start_thresh, max_start_thresh)。

数学本质：
  RMS = sqrt((1/N) * sum(x_i^2))，信号瞬时能量度量。
  滞回阈值 start_thresh > end_thresh，两阈值间留缓冲带，
  避免单阈值时噪声在阈值附近波动反复触发 start/end（边界抖动）。
  底噪 EMA：上升慢（防语音/瞬态噪声拉高）、下降快（环境变安静即回落）。

形象理解：
  像声音的"音量水位线"。超过高位（start_thresh）认为有人说话，
  低于低位（end_thresh）持续一段时间（silence_ms）认为说完了。
  高低位之间留"缓冲带"防抖；水位线本身随房间安静程度上下浮动。
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from config import VAD_PARAMS


@dataclass
class VADResult:
    """单帧 VAD 检测结果。"""
    utterance_started: bool = False   # 本帧触发了"开始说话"
    utterance_ended: bool = False     # 本帧触发了"一句话结束"
    rms: float = 0.0


class VADDetector:
    """RMS 阈值滞回 VAD 状态机（底噪自适应）。

    状态：
    - 待机：未录音，监测 start_thresh（随底噪浮动）
    - 录音中：已开始，监测 end_thresh + silence_ms / max_utterance_ms
    """

    # 底噪 EMA 系数（三档）：低于 floor 快速回落（环境变安静）；
    # 接近 floor 中速上升（真实持续的轻噪声）；疑似语音帧（显著超阈值）
    # 几乎不更新 —— 防止「触发→半双工 reset→再触发」循环把 floor 拉高，
    # 吞掉后续正常音量的说话
    _NOISE_DOWN_ALPHA = 0.15
    _NOISE_UP_ALPHA = 0.05
    _NOISE_SPEECH_ALPHA = 0.002
    _SPEECH_RATIO = 1.2

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_size: int = 1024,
        params: dict | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_size = frame_size
        p = params or VAD_PARAMS
        self.start_thresh = float(p["start_thresh"])
        self.end_thresh = float(p["end_thresh"])
        self.start_frames = int(p["start_frames"])
        self.silence_ms = int(p["silence_ms"])
        self.max_utterance_ms = int(p["max_utterance_ms"])
        # 自适应阈值边界：下限防死设备底噪误触发，上限防强噪声环境阈值飞高
        self.min_start_thresh = float(p.get("min_start_thresh", 0.004))
        self.max_start_thresh = float(p.get("max_start_thresh", 0.03))
        self.noise_ratio = float(p.get("noise_ratio", 4.0))
        self._noise_floor = 0.0

        self._start_frame_count = 0
        self._silent_frame_count = 0
        self._recording = False
        self._utterance_start_ms = 0
        self._now_ms = 0
        self._frame_ms = frame_size * 1000 / sample_rate

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def noise_floor(self) -> float:
        return self._noise_floor

    @property
    def current_start_thresh(self) -> float:
        """当前生效的开始阈值（含自适应）。波形显示等 UI 用它做分母。"""
        return self._adaptive_start_thresh()

    @staticmethod
    def compute_rms(samples: np.ndarray) -> float:
        """RMS = sqrt(mean(x^2))。"""
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))

    def _track_noise_floor(self, rms: float) -> None:
        """待机态底噪 EMA 跟踪（录音中不更新，天然防语音污染）。"""
        if self._noise_floor <= 0.0:
            self._noise_floor = max(rms, 1e-5)
            return
        if rms < self._noise_floor:
            alpha = self._NOISE_DOWN_ALPHA
        elif rms > self._adaptive_start_thresh() * self._SPEECH_RATIO:
            alpha = self._NOISE_SPEECH_ALPHA
        else:
            alpha = self._NOISE_UP_ALPHA
        self._noise_floor = (1.0 - alpha) * self._noise_floor + alpha * rms

    def _adaptive_start_thresh(self) -> float:
        """当前自适应开始阈值：底噪 × 倍数，夹在 [min, max]。"""
        if self._noise_floor <= 0.0:
            return self.start_thresh
        return min(
            self.max_start_thresh,
            max(self.min_start_thresh, self._noise_floor * self.noise_ratio),
        )

    def feed(self, samples: np.ndarray) -> VADResult:
        """喂一帧音频，返回本帧检测结果。"""
        rms = self.compute_rms(samples)
        self._now_ms += self._frame_ms
        result = VADResult(rms=rms)

        if not self._recording:
            # 待机：底噪跟踪 + 监测开始说话（阈值自适应浮动）
            self._track_noise_floor(rms)
            thresh = self._adaptive_start_thresh()
            if rms > thresh:
                self._start_frame_count += 1
            else:
                self._start_frame_count = 0
            if self._start_frame_count >= self.start_frames:
                self._recording = True
                self._utterance_start_ms = self._now_ms
                self._silent_frame_count = 0
                result.utterance_started = True
        else:
            # 录音中：监测结束（静音超时 / 超长录音），end 阈值随自适应 start 等比浮动
            if self._noise_floor > 0.0:
                end_thresh = self._adaptive_start_thresh() * (self.end_thresh / self.start_thresh)
            else:
                end_thresh = self.end_thresh
            if rms < end_thresh:
                self._silent_frame_count += 1
            else:
                self._silent_frame_count = 0
            silent_ms = self._silent_frame_count * self._frame_ms
            elapsed = self._now_ms - self._utterance_start_ms
            if silent_ms >= self.silence_ms or elapsed >= self.max_utterance_ms:
                result.utterance_ended = True
                self._recording = False
                self._start_frame_count = 0
                self._silent_frame_count = 0
        return result

    def reset(self) -> None:
        """重置状态机（半双工切换时调用）。底噪估计保留（环境没变）。"""
        self._start_frame_count = 0
        self._silent_frame_count = 0
        self._recording = False
        self._utterance_start_ms = 0
        self._now_ms = 0
