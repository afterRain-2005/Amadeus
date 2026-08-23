# tests/test_phone_config.py
from config import PHONE_DEFAULTS, VAD_PARAMS

def test_phone_defaults_keys():
    assert "vision_endpoint" in PHONE_DEFAULTS
    assert "vision_api_key" in PHONE_DEFAULTS
    assert "vision_model" in PHONE_DEFAULTS          # 默认 gpt-4o
    assert "gpt_sovits_url" in PHONE_DEFAULTS         # 默认 http://127.0.0.1:9880
    assert "screen_share_default" in PHONE_DEFAULTS   # 默认 True
    assert "capture_interval_ms" in PHONE_DEFAULTS    # 默认 2500

def test_vad_params_match_original():
    """VAD 参数移植原项目 VoiceCall.tsx:23-27。

    差异两处（弱信号麦克风实测 necessity，见 config.py VAD_PARAMS 注释）：
    - start_frames 3→2：无浏览器 AGC 的原始说话帧时高时低（0.001-0.005），
      连续 3 帧超阈值常凑不齐 → 永不触发（2026-08-22 voice_call.log 实证）
    - min_start_thresh=0.0015：新增下限键，非原项目参数
    """
    assert VAD_PARAMS["start_thresh"] == 0.018
    assert VAD_PARAMS["end_thresh"] == 0.012
    assert VAD_PARAMS["start_frames"] == 2
    assert VAD_PARAMS["silence_ms"] == 1100
    assert VAD_PARAMS["max_utterance_ms"] == 15000
    assert VAD_PARAMS["min_start_thresh"] == 0.0015

def test_vision_model_default_gpt4o():
    assert PHONE_DEFAULTS["vision_model"] == "gpt-4o"
