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


# ===== 底噪自适应阈值（软件替代浏览器 autoGainControl）=====

def test_weak_mic_speech_triggers_after_noise_calibration():
    """弱信号麦克风（无 AGC 原始电平）：底噪 ~0.0005、说话 RMS ~0.005
    （实测阵列麦克风场景）。固定 0.018 阈值永不触发；自适应阈值应下探
    到 max(0.0015, 0.0005×4)=0.002 触发。"""
    det = VADDetector()
    # 60 帧（~4s）底噪，让 noise_floor 收敛
    for _ in range(60):
        det.feed(_frame(0.0005))
    assert det.noise_floor < 0.002
    assert det.current_start_thresh <= 0.002  # 下探到下限
    # 说话 0.005：超过自适应阈值，连续帧触发
    started = False
    for _ in range(6):
        if det.feed(_frame(0.005)).utterance_started:
            started = True
    assert started, "弱信号说话未触发（自适应阈值未生效）"


def test_very_weak_speech_triggers():
    """极弱说话（大多帧 0.001-0.004、峰值 0.0046，实测 2026-08-22 用户
    机器 device=1 场景）：min_start_thresh=0.0015 + start_frames=2 下应能触发。"""
    det = VADDetector()
    for _ in range(60):
        det.feed(_frame(0.00005))   # 死寂底噪
    assert det.current_start_thresh <= 0.0015
    # 模拟弱说话帧序列（时高时低，最低 0.0016）
    speech = [0.0046, 0.0016, 0.003, 0.002, 0.0046, 0.0018, 0.003]
    started = any(det.feed(_frame(r)).utterance_started for r in speech)
    assert started, "极弱说话未触发（下限/连帧数不够灵敏）"


def test_dead_device_does_not_false_trigger():
    """死设备/极静环境（RMS ~0.00002）：阈值有 0.0015 下限，不误触发。"""
    det = VADDetector()
    for _ in range(100):
        det.feed(_frame(0.00002))
    assert det.current_start_thresh >= 0.0015
    result = det.feed(_frame(0.00003))
    assert not result.utterance_started


def test_noisy_environment_raises_threshold():
    """强噪声环境（底噪 ~0.01）：阈值升到 ~0.04 封顶 0.03，普通说话
    （0.015）不误触发，大声（0.05）仍能触发。"""
    det = VADDetector()
    for _ in range(200):  # 上升慢，需足够帧收敛
        det.feed(_frame(0.01))
    assert det.current_start_thresh > 0.02  # 显著高于下限
    # 大声说话仍可触发
    started = any(det.feed(_frame(0.06)).utterance_started for _ in range(6))
    assert started


def test_speech_does_not_permanently_raise_noise_floor():
    """语音帧不应污染底噪：触发后进入录音态（floor 冻结不更新，真实链路
    中 utterance 结束即切 processing 暂停 VAD），静默后 floor 快速回落。"""
    det = VADDetector()
    for _ in range(60):
        det.feed(_frame(0.0005))
    floor_before = det.noise_floor
    # 说话 78 帧（~5s）：触发录音后 recording 态不 track floor
    floor_at_start = None
    for _ in range(78):
        det.feed(_frame(0.005))
        if det.is_recording and floor_at_start is None:
            floor_at_start = det.noise_floor
    assert det.is_recording
    assert det.noise_floor == floor_at_start  # 录音期间 floor 冻结
    # 静默 80 帧：utterance 结束 + floor 快速回落
    for _ in range(80):
        det.feed(_frame(0.0005))
    assert not det.is_recording
    assert det.current_start_thresh <= 0.008
