# tests/test_vad.py
import numpy as np
from core.vad import VADDetector

def _frame(rms_target: float, samples: int = 1024) -> np.ndarray:
    """生成指定 RMS 的帧（白噪声缩放）。"""
    noise = np.random.randn(samples).astype(np.float32)
    current_rms = float(np.sqrt(np.mean(noise ** 2)))
    if current_rms == 0:
        return noise
    return noise * (rms_target / current_rms)

def test_silence_does_not_start():
    """持续静音（低于 end_thresh）不应触发 start。"""
    det = VADDetector()
    for _ in range(20):
        result = det.feed(_frame(0.005))
    assert not result.utterance_started

def test_loud_starts_utterance():
    """连续 3 帧超 start_thresh 触发 start。"""
    det = VADDetector()
    started = False
    for _ in range(5):
        result = det.feed(_frame(0.05))  # 远超 0.018
        if result.utterance_started:
            started = True
    assert started

def test_silence_after_speech_ends_utterance():
    """说话后静音 silence_ms 触发 end。"""
    det = VADDetector()
    # 先触发 start
    for _ in range(5):
        det.feed(_frame(0.05))
    # 静音结束（每帧 ~16ms @1024 samples/16kHz，需 ~69 帧达 1100ms）
    ended = False
    for _ in range(80):
        result = det.feed(_frame(0.005))
        if result.utterance_ended:
            ended = True
            break
    assert ended

def test_reset_clears_state():
    det = VADDetector()
    for _ in range(5):
        det.feed(_frame(0.05))
    det.reset()
    assert det.is_recording is False
