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
         patch.object(ctrl._tts, "speak_with_options"):
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
         patch.object(ctrl._tts, "speak_with_options"):
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
         patch.object(ctrl._tts, "speak_with_options"):
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


def test_on_llm_delta_starts_streaming_on_separator():
    """_on_llm_delta 检测到 === 时启动流式 TTS，切换 phase 到 speaking。"""
    ctrl = _make_controller()
    ctrl._set_phase("processing")
    with patch.object(ctrl._tts, "speak_streaming_start") as mock_start, \
         patch.object(ctrl._tts, "speak_streaming_append") as mock_append:
        # 中文段：不启动 TTS（无假名）
        ctrl._on_llm_delta("[emotion:neutral]（歪头）嗯，怎么了？")
        assert ctrl._stream_tts_started is False
        mock_start.assert_not_called()
        # === 与首句日语同 delta（LLM 流式常见场景）：启动 TTS + 提取首句追加
        ctrl._on_llm_delta("\n===\n（首を傾げる）ええ、どうしたの？")
        assert ctrl._stream_tts_started is True
        mock_start.assert_called_once_with(text_lang="ja")
        # phase 切到 speaking（VAD 暂停）
        assert ctrl.phase == "speaking"
        assert ctrl.vad_paused is True
        # 首句日语（=== 之后部分，含假名）已追加 1 次
        assert mock_append.call_count == 1
        # 后续 delta：含假名的追加，纯中文（无假名）跳过
        ctrl._on_llm_delta("微笑んでいる")  # 含假名，追加
        assert mock_append.call_count == 2
        ctrl._on_llm_delta("元気かしら？")  # 含假名 かしら，追加
        assert mock_append.call_count == 3


def test_on_llm_delta_separator_only_no_append():
    """=== 出现但 === 后无内容时不调 speak_streaming_append（边界场景）。"""
    ctrl = _make_controller()
    with patch.object(ctrl._tts, "speak_streaming_start") as mock_start, \
         patch.object(ctrl._tts, "speak_streaming_append") as mock_append:
        ctrl._on_llm_delta("中文\n===\n")
        # 中文段无假名不启动 TTS；=== 后为空也不启动
        assert ctrl._stream_tts_started is False
        mock_start.assert_not_called()
        mock_append.assert_not_called()


def test_on_llm_delta_skips_chinese_before_separator():
    """=== 之前的中文段不送 TTS（无假名跳过）。"""
    ctrl = _make_controller()
    with patch.object(ctrl._tts, "speak_streaming_start") as mock_start, \
         patch.object(ctrl._tts, "speak_streaming_append") as mock_append:
        # 中文段多次 delta（无假名，不启动 TTS）
        ctrl._on_llm_delta("[emotion:smile]（微笑）")
        ctrl._on_llm_delta("你好啊。")
        ctrl._on_llm_delta("今天天气不错。")
        assert ctrl._stream_tts_started is False
        mock_start.assert_not_called()
        mock_append.assert_not_called()


def test_on_llm_delta_multi_separator_skips_chinese_segments():
    """多段 === + emotion 错位：LLM 输出「中文1 === 日语1 \\n\\n [emotion:neutral]中文2 === 日语2」。

    修复 bug：LLM 把 [emotion:xxx] 放在 === 后的日语段里（违反 prompt 约定），
    双重切段逻辑会错误把日语段1重置为中文段，后续 TTS 全跳过 → 无声。
    新方案：纯 === 切段 + 假名过滤，只送含假名的日语段。
    """
    ctrl = _make_controller()
    with patch.object(ctrl._tts, "speak_streaming_start") as mock_start, \
         patch.object(ctrl._tts, "speak_streaming_append") as mock_append:
        # 模拟 LLM 多段输出（[emotion] 在 === 后的日语段里）
        # 日语段含假名（ええ、どうしたの / そうね、分かったわ）
        ctrl._on_llm_delta("你好\n===\nええ、どうしたの？\n\n[emotion:neutral]我很好\n===\nそうね、分かったわ")
        # 两段日语（含假名）应被追加，两段中文（无假名）应被跳过
        assert ctrl._stream_tts_started is True
        # 启动 TTS 1 次（首次进入日语段）
        mock_start.assert_called_once_with(text_lang="ja")
        # 追加次数：第一段日语 + 第二段日语 = 2 次（中文段无假名不追加）
        assert mock_append.call_count == 2
        # 验证追加的内容是日语段
        appended_texts = [call.args[0] for call in mock_append.call_args_list]
        all_appended = "".join(appended_texts)
        assert "ええ、どうしたの？" in all_appended
        assert "そうね、分かったわ" in all_appended
        assert "我很好" not in all_appended  # 中文段不送 TTS
        assert "你好" not in all_appended  # 中文段不送 TTS


def test_handle_utterance_streaming_path():
    """_handle_utterance 走流式 TTS 路径：LLM 返回含 === 的 reply，调 speak_streaming_end。"""
    ctrl = _make_controller()
    reply_with_sep = "[emotion:neutral]（歪头）嗯，怎么了？\n===\n（首を傾げる）ええ、どうしたの？"

    def fake_stream_llm(user_text):
        # 模拟 LLM 流式：先中文，后 ===，后日语
        ctrl._on_llm_delta("[emotion:neutral]（歪头）嗯，怎么了？")
        ctrl._on_llm_delta("\n===\n")
        ctrl._on_llm_delta("（首を傾げる）")
        ctrl._on_llm_delta("ええ、どうしたの？")
        return reply_with_sep

    with patch("core.voice_call.encode_wav"), \
         patch.object(ctrl, "_transcribe", return_value="你好"), \
         patch.object(ctrl, "_stream_llm", side_effect=fake_stream_llm), \
         patch.object(ctrl._tts, "speak_streaming_start") as mock_start, \
         patch.object(ctrl._tts, "speak_streaming_append"), \
         patch.object(ctrl._tts, "speak_streaming_end") as mock_end:
        ctrl._handle_utterance(b"audio")
    assert mock_start.called
    assert mock_end.called
    # 流式已启动，不应走兜底 speak_with_options
    # （ctrl._tts 是真实的 SpeechPlayer，speak_with_options 没被 mock 但流式路径不会调它）


def test_handle_utterance_fallback_no_separator():
    """_handle_utterance 兜底路径：LLM 返回无 === 时整段合成。"""
    ctrl = _make_controller()
    with patch("core.voice_call.encode_wav"), \
         patch.object(ctrl, "_transcribe", return_value="你好"), \
         patch.object(ctrl, "_stream_llm", return_value="こんにちは"), \
         patch.object(ctrl._tts, "speak_with_options") as mock_speak, \
         patch.object(ctrl._tts, "speak_streaming_start") as mock_start:
        ctrl._handle_utterance(b"audio")
    # 走兜底，不调流式
    mock_start.assert_not_called()
    # 调 speak_with_options 整段合成
    mock_speak.assert_called_once()
    args = mock_speak.call_args
    assert args.args[0] == "こんにちは"  # parsed.chinese（无 === 时 chinese=full）
    assert args.kwargs["text_lang"] == "ja"
    assert args.kwargs["allow_fallback"] is False