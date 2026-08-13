# core/vad.py
"""VAD（Voice Activity Detection）：RMS 振幅阈值滞回状态机。

移植原项目 amadeus/src/components/VoiceCall.tsx:23-27 的参数与逻辑。

数学本质：
  RMS = sqrt((1/N) * sum(x_i^2))，信号瞬时能量度量。
  滞回阈值 start_thresh > end_thresh，两阈值间留缓冲带，
  避免单阈值时噪声在阈值附近波动反复触发 start/end（边界抖动）。

形象理解：
  像声音的"音量水位线"。超过高位（start_thresh）认为有人说话，
  低于低位（end_thresh）持续一段时间（silence_ms）认为说完了。
  高低位之间留"缓冲带"防抖。
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
    """RMS 阈值滞回 VAD 状态机。

    状态：
    - 待机：未录音，监测 start_thresh
    - 录音中：已开始，监测 end_thresh + silence_ms / max_utterance_ms
    """

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

        self._start_frame_count = 0
        self._silent_frame_count = 0
        self._recording = False
        self._utterance_start_ms = 0
        self._now_ms = 0
        self._frame_ms = frame_size * 1000 / sample_rate

    @property
    def is_recording(self) -> bool:
        return self._recording

    @staticmethod
    def compute_rms(samples: np.ndarray) -> float:
        """RMS = sqrt(mean(x^2))。"""
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))

    def feed(self, samples: np.ndarray) -> VADResult:
        """喂一帧音频，返回本帧检测结果。"""
        rms = self.compute_rms(samples)
        self._now_ms += self._frame_ms
        result = VADResult(rms=rms)

        if not self._recording:
            # 待机：监测开始说话
            if rms > self.start_thresh:
                self._start_frame_count += 1
            else:
                self._start_frame_count = 0
            if self._start_frame_count >= self.start_frames:
                self._recording = True
                self._utterance_start_ms = self._now_ms
                self._silent_frame_count = 0
                result.utterance_started = True
        else:
            # 录音中：监测结束（静音超时 / 超长录音）
            if rms < self.end_thresh:
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
        """重置状态机（半双工切换时调用）。"""
        self._start_frame_count = 0
        self._silent_frame_count = 0
        self._recording = False
        self._utterance_start_ms = 0
        self._now_ms = 0
