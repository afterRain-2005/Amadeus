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
    """VAD 参数移植原项目 VoiceCall.tsx:23-27。"""
    assert VAD_PARAMS["start_thresh"] == 0.018
    assert VAD_PARAMS["end_thresh"] == 0.012
    assert VAD_PARAMS["start_frames"] == 3
    assert VAD_PARAMS["silence_ms"] == 1100
    assert VAD_PARAMS["max_utterance_ms"] == 15000

def test_vision_model_default_gpt4o():
    assert PHONE_DEFAULTS["vision_model"] == "gpt-4o"
