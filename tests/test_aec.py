# tests/test_aec.py — 软件回声消除（core/aec.py）测试
"""合成回声验证：参考信号卷积随机房间脉冲响应生成"回声"，
NLMS 滤波器应显著衰减（ERLE），且参数可调、不可用时正确旁通。"""
import numpy as np
import pytest

from core.aec import AECFilter, EchoReference


SR, N = 16000, 1024


def _make_scene(seed: int = 42, seconds: float = 6.0, user_speech=False):
    """合成远端参考 + 房间回声麦克风信号。"""
    rng = np.random.default_rng(seed)
    rir = np.zeros(1920, dtype=np.float32)
    d0 = int(0.06 * SR)
    rir[d0] = 0.7
    rir[d0 + 50 : d0 + 400] = rng.standard_normal(350).astype(np.float32) * 0.05
    n = int(SR * seconds)
    ref = rng.standard_normal(n).astype(np.float32) * 0.05
    mic = np.convolve(ref, rir)[:n]
    if user_speech:  # 双讲：混入用户语音（AEC 不应把它消掉）
        mic = mic + rng.standard_normal(n).astype(np.float32) * 0.02
    return ref, mic


def _erle_db(d: np.ndarray, e: np.ndarray) -> float:
    return 10.0 * np.log10((float(np.dot(d, d)) + 1e-12) / (float(np.dot(e, e)) + 1e-12))


def test_aec_suppresses_synthetic_echo():
    """核心：合成回声经 AEC 后衰减 ≥20dB（收敛后）。"""
    ref, mic = _make_scene()
    aec = AECFilter(filter_len_ms=120, mu=0.5, align_delay_ms=0, nlp_gain=0.0)
    L = aec.filter_len
    tail_erle = 0.0
    for i in range(0, mic.size - N - L + 1, N):
        d = mic[i + L - 1 : i + L - 1 + N]
        e = aec.process(d, ref[i : i + N + L - 1])
        tail_erle = _erle_db(d, e)
    assert tail_erle > 20.0, f"回声衰减不足: {tail_erle:.1f}dB"
    assert aec.converged


def test_aec_preserves_doubletalk_speech():
    """双讲保护：回声中混入的用户语音能量应大部分保留。"""
    ref, mic = _make_scene(seed=7, user_speech=True)
    # 重建纯用户分量（同 seed 重放随机序列）
    rng = np.random.default_rng(7)
    _ = rng.standard_normal(350)  # 消耗 rir 随机数
    n = mic.size
    user = rng.standard_normal(n).astype(np.float32) * 0.02
    aec = AECFilter(filter_len_ms=120, mu=0.5, align_delay_ms=0, nlp_gain=0.0)
    L = aec.filter_len
    # 前半段纯回声收敛
    for i in range(0, n // 2 - N - L + 1, N):
        aec.process(mic[i + L - 1 : i + L - 1 + N], ref[i : i + N + L - 1])
    # 后半段双讲：误差信号应保留用户语音量级（不衰减超过一半能量）
    kept_ratio = []
    for i in range(n // 2, n - N - L + 1, N):
        d = mic[i + L - 1 : i + L - 1 + N]
        u = user[i + L - 1 : i + L - 1 + N]
        e = aec.process(d, ref[i : i + N + L - 1])
        kept_ratio.append(float(np.dot(e, e)) / (float(np.dot(u, u)) + 1e-12))
    ratio = float(np.mean(kept_ratio))
    assert 0.3 < ratio < 3.0, f"双讲语音保真异常: kept/user={ratio:.2f}"


def test_aec_params_adjustable():
    """参数热调：改滤波长度/μ 后状态重建（抽头数变化重置滤波器）。"""
    aec = AECFilter(filter_len_ms=120, mu=0.5, align_delay_ms=80)
    assert aec.filter_len == 1920
    aec.set_params(filter_len_ms=200, mu=0.2, align_delay_ms=40)
    assert aec.filter_len == 3200
    assert aec.align_delay == 640


def test_aec_passthrough_when_ref_missing():
    """参考不足（冷启动）时旁通：原样返回麦克风帧。"""
    aec = AECFilter(filter_len_ms=120)
    frame = np.ones(256, dtype=np.float32) * 0.1
    out = aec.process(frame, None)
    assert out is frame


def test_echo_reference_ring_and_resample():
    """参考缓冲：任意采样率 push（内部重采样 16k），窗口读取正确回放。"""
    ref = EchoReference()
    # 48k 信号 0.5s → 16k 应得 ~8000 样本
    sig = np.ones(24000, dtype=np.float32) * 0.5
    ref.push(sig, 48000)
    win = ref.window(8000)
    assert win is not None and win.size == 8000
    assert np.allclose(win, 0.5, atol=1e-6)
    assert ref.playing()
    # 环形覆盖：容量 2s，push 3s 数据后旧数据被覆盖但窗口自洽
    ref.push(np.zeros(48000, dtype=np.float32), 16000)  # 3s
    win2 = ref.window(1000)
    assert win2 is not None and np.allclose(win2, 0.0)


def test_echo_reference_window_insufficient():
    """缓冲未满时 window 返回 None（调用方回退）。"""
    ref = EchoReference()
    assert ref.window(100) is None
    assert not ref.playing()


def test_aec_disabled_in_controller():
    """VoiceCallController：aec.enabled=False 时不建 AEC、TTS 不采集参考。"""
    from unittest.mock import patch
    from core.voice_call import VoiceCallController
    ctrl = VoiceCallController({
        "endpoint": "https://api.test/v1", "api_key": "k", "model": "m",
        "aec": {"enabled": False},
    })
    assert ctrl._aec is None
    assert ctrl._echo_ref is None
    assert ctrl._tts.echo_ref is None


def test_aec_controller_wiring():
    """启用时 controller 注入参考到 TTS，_aec_process 未收敛返回 None（回退 barge-in）。"""
    from core.voice_call import VoiceCallController
    ctrl = VoiceCallController({
        "endpoint": "https://api.test/v1", "api_key": "k", "model": "m",
    })
    assert ctrl._aec is not None
    assert ctrl._tts.echo_ref is ctrl._echo_ref
    # 无参考数据时：旁通（回退路径）
    frame = np.ones(1024, dtype=np.float32) * 0.01
    assert ctrl._aec_process(frame) is None
