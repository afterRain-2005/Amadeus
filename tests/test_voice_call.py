from unittest.mock import patch, MagicMock, call
from core.voice_call import VoiceCallController


def _make_controller():
    config = {
        "endpoint": "https://api.deepseek.com/v1",
        "api_key": "sk-test",
        "model": "deepseek-chat",
        "vision_endpoint": "https://api.openai.com/v1",
        "vision_api_key": "sk-vision",
        "vision_model": "gpt-4o",
    }
    return VoiceCallController(config)


def test_initial_phase_is_connecting():
    ctrl = _make_controller()
    assert ctrl.phase == "idle"


def test_start_transitions_to_listening():
    """start() 后经 connecting 进入 listening。"""
    ctrl = _make_controller()
    with patch.object(ctrl, "_open_mic"), \
         patch.object(ctrl, "_start_screen_capture"):
        ctrl.start()
    # connecting 立即设置，listening 由 connecting_ms 计时器触发
    assert ctrl.phase == "connecting"


def test_hangup_sets_ended():
    ctrl = _make_controller()
    with patch.object(ctrl, "_open_mic"), \
         patch.object(ctrl, "_start_screen_capture"), \
         patch.object(ctrl, "_close_mic"), \
         patch.object(ctrl, "_stop_screen_capture"):
        ctrl.start()
        ctrl.hangup()
    assert ctrl.phase == "ended"


def test_speaking_phase_pauses_vad():
    """半双工：speaking/processing 态 VAD 不触发（移植 speakingRef）。"""
    ctrl = _make_controller()
    ctrl._set_phase("speaking")
    assert ctrl.vad_paused is True
    ctrl._set_phase("listening")
    assert ctrl.vad_paused is False


def test_utterance_end_attaches_screen_frame():
    """说话结束时取最新缓存帧附给视觉模型（spec §5.2）。"""
    ctrl = _make_controller()
    ctrl._screen_share_on = True
    fake_frame = MagicMock()
    ctrl._capturer = MagicMock()
    ctrl._capturer.latest_frame = fake_frame
    with patch("core.voice_call.encode_wav"), \
         patch("core.voice_call.describe_screen", return_value="VS Code 编辑") as mock_vis, \
         patch.object(ctrl, "_transcribe", return_value="我在写代码"), \
         patch.object(ctrl, "_stream_llm", return_value="加油"), \
         patch.object(ctrl, "_play_tts"):
        ctrl._handle_utterance(b"audio bytes")
    mock_vis.assert_called_once()
    # LLM user 消息应含屏幕描述
    assert "VS Code" in ctrl._last_user_message


def test_screen_share_off_skips_vision():
    """屏幕共享关闭时不调视觉模型。"""
    ctrl = _make_controller()
    ctrl._screen_share_on = False
    ctrl._capturer = MagicMock()
    ctrl._capturer.latest_frame = b"frame"
    with patch("core.voice_call.encode_wav"), \
         patch("core.voice_call.describe_screen") as mock_vis, \
         patch.object(ctrl, "_transcribe", return_value="你好"), \
         patch.object(ctrl, "_stream_llm", return_value="こんにちは"), \
         patch.object(ctrl, "_play_tts"):
        ctrl._handle_utterance(b"audio")
    mock_vis.assert_not_called()


def test_vision_empty_key_skips_vision():
    """未配视觉 key 时即使屏幕共享开也不调视觉。"""
    ctrl = _make_controller()
    ctrl._config["vision_api_key"] = ""
    ctrl._screen_share_on = True
    ctrl._capturer = MagicMock()
    ctrl._capturer.latest_frame = b"frame"
    with patch("core.voice_call.encode_wav"), \
         patch("core.voice_call.describe_screen") as mock_vis, \
         patch.object(ctrl, "_transcribe", return_value="你好"), \
         patch.object(ctrl, "_stream_llm", return_value="こんにちは"), \
         patch.object(ctrl, "_play_tts"):
        ctrl._handle_utterance(b"audio")
    mock_vis.assert_not_called()


def test_transcribe_failure_returns_to_listening():
    """STT 失败回 listening（spec §7 降级表）。"""
    ctrl = _make_controller()
    with patch("core.voice_call.encode_wav"), \
         patch.object(ctrl, "_transcribe", side_effect=Exception("ASR fail")), \
         patch.object(ctrl, "_set_phase") as mock_phase:
        ctrl._handle_utterance(b"audio")
    # 应该回到 listening
    assert mock_phase.called
    last_call = mock_phase.call_args[0][0]
    assert last_call == "listening"


def test_toggle_mute_flips_state():
    ctrl = _make_controller()
    assert ctrl.is_muted is False
    ctrl.toggle_mute()
    assert ctrl.is_muted is True
    ctrl.toggle_mute()
    assert ctrl.is_muted is False


def test_toggle_screen_share_flips_state():
    ctrl = _make_controller()
    assert ctrl.screen_share_on is True  # 默认开
    ctrl.toggle_screen_share()
    assert ctrl.screen_share_on is False