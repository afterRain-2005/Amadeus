from unittest.mock import patch, MagicMock, call
from core.voice.voice_call import VoiceCallController


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


def test_start_stops_when_mic_fails():
    ctrl = _make_controller()

    def fail_open_mic():
        ctrl._set_phase("ended")

    with patch.object(ctrl, "_open_mic", side_effect=fail_open_mic), \
         patch.object(ctrl, "_start_screen_capture") as mock_capture:
        ctrl.start()

    assert ctrl.phase == "ended"
    assert not ctrl._elapsed_timer.isActive()
    assert not ctrl._connecting_timer.isActive()
    mock_capture.assert_not_called()


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
    """全双工：仅 speaking 暂停主 VAD（她说话时回声会误触发）；
    processing（网络等待期扬声器无声）放开 VAD，用户可直接改口。"""
    ctrl = _make_controller()
    ctrl._set_phase("speaking")
    assert ctrl.vad_paused is True
    ctrl._set_phase("processing")
    assert ctrl.vad_paused is False
    ctrl._set_phase("listening")
    assert ctrl.vad_paused is False


def test_utterance_end_attaches_screen_frame():
    """说话结束时取最新缓存帧附给视觉模型（spec §5.2）。"""
    ctrl = _make_controller()
    ctrl._screen_share_on = True
    fake_frame = MagicMock()
    ctrl._capturer = MagicMock()
    ctrl._capturer.latest_frame = fake_frame
    with patch("core.voice.voice_call.encode_wav"), \
         patch("core.voice.voice_call.describe_screen", return_value="VS Code 编辑") as mock_vis, \
         patch.object(ctrl, "_transcribe", return_value="我在写代码"), \
         patch.object(ctrl, "_stream_llm", return_value="加油"), \
         patch.object(ctrl._tts, "speak_with_options"):
        ctrl._handle_utterance(b"audio bytes")
    mock_vis.assert_called_once()
    assert mock_vis.call_args.args[0] is fake_frame
    # LLM user 消息应含屏幕描述
    assert "VS Code" in ctrl._last_user_message


def test_handle_utterance_uses_actual_mic_sample_rate():
    """设备 fallback 到默认采样率时，WAV header 应使用真实采样率。"""
    ctrl = _make_controller()
    ctrl._mic_sample_rate = 48000
    with patch("core.voice.voice_call.encode_wav") as mock_encode, \
         patch.object(ctrl, "_transcribe", return_value="你好"), \
         patch.object(ctrl, "_stream_llm", return_value="こんにちは"), \
         patch.object(ctrl._tts, "speak_with_options"):
        ctrl._handle_utterance(b"audio")
    mock_encode.assert_called_once()
    assert mock_encode.call_args.args[1] == 48000


def test_screen_share_off_skips_vision():
    """屏幕共享关闭时不调视觉模型。"""
    ctrl = _make_controller()
    ctrl._screen_share_on = False
    ctrl._capturer = MagicMock()
    ctrl._capturer.latest_frame = b"frame"
    with patch("core.voice.voice_call.encode_wav"), \
         patch("core.voice.voice_call.describe_screen") as mock_vis, \
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
    with patch("core.voice.voice_call.encode_wav"), \
         patch("core.voice.voice_call.describe_screen") as mock_vis, \
         patch.object(ctrl, "_transcribe", return_value="你好"), \
         patch.object(ctrl, "_stream_llm", return_value="こんにちは"), \
         patch.object(ctrl._tts, "speak_with_options"):
        ctrl._handle_utterance(b"audio")
    mock_vis.assert_not_called()


def test_transcribe_failure_returns_to_listening():
    """STT 失败回 listening（spec §7 降级表）。"""
    ctrl = _make_controller()
    with patch("core.voice.voice_call.encode_wav"), \
         patch.object(ctrl, "_transcribe", side_effect=Exception("ASR fail")), \
         patch.object(ctrl, "_set_phase") as mock_phase:
        ctrl._handle_utterance(b"audio")
    # 应该回到 listening
    assert mock_phase.called
    last_call = mock_phase.call_args[0][0]
    assert last_call == "listening"


def test_toggle_mute_flips_state():
    ctrl = _make_controller()
    states = []
    ctrl.muted_changed.connect(states.append)
    assert ctrl.is_muted is False
    ctrl.toggle_mute()
    assert ctrl.is_muted is True
    ctrl.toggle_mute()
    assert ctrl.is_muted is False
    assert states == [True, False]


def test_toggle_screen_share_flips_state():
    ctrl = _make_controller()
    states = []
    ctrl.screen_share_changed.connect(states.append)
    assert ctrl.screen_share_on is True  # 默认开
    ctrl.toggle_screen_share()
    assert ctrl.screen_share_on is False
    assert states == [False]


def test_on_llm_delta_only_accumulates():
    """逐句协议：_on_llm_delta 只累积全文，不再自动启动流式 TTS 口播。"""
    ctrl = _make_controller()
    ctrl._active_turn = ctrl._turn_id
    with patch.object(ctrl._tts, "speak_streaming_start") as mock_start, \
         patch.object(ctrl._tts, "speak_streaming_append") as mock_append:
        ctrl._on_llm_delta("[emotion:neutral]（歪头）嗯，怎么了？")
        ctrl._on_llm_delta("\n===\n（首を傾げる）ええ、どうしたの？")
    # 只累积，不播
    mock_start.assert_not_called()
    mock_append.assert_not_called()
    assert ctrl._stream_tts_started is False
    assert "嗯，怎么了？" in ctrl._streamed_reply
    assert "ええ、どうしたの？" in ctrl._streamed_reply


def test_prepare_reply_segments_and_emits_first():
    """_prepare_reply 切成中文字幕句 + 日语句，发 reply_show 显示第一句，不自动播。"""
    ctrl = _make_controller()
    shown: list[tuple[int, int, str]] = []
    ctrl.reply_show.connect(lambda i, t, s: shown.append((i, t, s)))
    with patch.object(ctrl._tts, "speak_with_options") as mock_speak, \
         patch.object(ctrl, "_set_phase") as mock_phase:
        ctrl._prepare_reply("[emotion:neutral]（歪头）嗯，怎么了？\n===\n（首を傾げる）ええ、どうしたの？")
    assert shown, "第一句未显示"
    assert shown[0][1] > 0  # total>0（分句态）
    assert "嗯，怎么了？" in shown[0][2]  # 第一句为中文字幕
    # 进入分句态但不自动朗读，等点击
    mock_speak.assert_not_called()
    mock_phase.assert_any_call("listening")


def test_advance_reply_drives_speech_and_progress():
    """点击推进：朗读当前句日语 + 显示下一字幕，逐句听写。"""
    ctrl = _make_controller()
    shown: list[str] = []
    ctrl.reply_show.connect(lambda i, t, s: shown.append(s))
    ctrl._prepare_reply("（歪头）今天怎么样？\n===\n（首を傾げる）今日はどう？")
    first = len(shown)
    with patch.object(ctrl._tts, "speak_with_options") as mock_speak:
        ctrl.advance_reply()
    # 第一次点击：显示并朗读第一句日语
    assert mock_speak.called
    args = mock_speak.call_args
    assert args.kwargs["text_lang"] == "ja"
    assert args.kwargs["fallback_lang"] == "zh"  # 中文兜底
    assert "今日はどう" in args.args[0]
    assert len(shown) == first + 1  # 显示推进
    assert ctrl.phase == "speaking"  # 半双工：朗读时 VAD 暂停


def test_advance_reply_after_end_returns_to_listening():
    """读到尾部后再点击 → 结束回放，恢复聆听态。"""
    ctrl = _make_controller()
    shown: list[str] = []
    ctrl.reply_show.connect(lambda i, t, s: shown.append(s))
    ctrl._prepare_reply("就一句。\n===\n一言だけ。")
    with patch.object(ctrl._tts, "speak_with_options"):
        ctrl.advance_reply()  # 读第 0 句
        ctrl.advance_reply()  # 到尾 → 回聆听
    assert ctrl.phase == "listening"
    assert ctrl._resp_index == -1
    assert ctrl._reply_review is False


def test_advance_reply_inactive_is_noop():
    """未进分句态/挂断后调用 advance_reply 为空操作。"""
    ctrl = _make_controller()
    with patch.object(ctrl._tts, "speak_with_options") as mock_speak:
        ctrl.advance_reply()
    mock_speak.assert_not_called()


def test_record_invokes_callback():
    """通话一问一答经 on_record 回调写入聊天会话（与普通聊天统一）。"""
    recorded: list[tuple[str, str]] = []
    ctrl = VoiceCallController({}, on_record=lambda r, c: recorded.append((r, c)))
    ctrl._record("user", "你好")
    ctrl._record("assistant", "[emotion:x]嗯\n===\nはい")
    assert recorded == [("user", "你好"), ("assistant", "[emotion:x]嗯\n===\nはい")]


def test_record_callback_exception_is_swallowed():
    """record 回调异常静默，不中断通话管线。"""
    def boom(role, content):
        raise RuntimeError("boom")
    ctrl = VoiceCallController({}, on_record=boom)
    ctrl._record("user", "你好")  # 不应抛异常


def test_handle_utterance_segment_and_record():
    """_handle_utterance 收尾：记录一问一答 + 分句显示，不再自动流式口播。"""
    ctrl = _make_controller()
    recorded: list[tuple[str, str]] = []
    ctrl._on_record = lambda r, c: recorded.append((r, c))
    reply_with_sep = "[emotion:neutral]（歪头）嗯，怎么了？\n===\n（首を傾げる）ええ、どうしたの？"
    shown: list[int] = []
    ctrl.reply_show.connect(lambda i, t, s: shown.append(i))
    with patch("core.voice.voice_call.encode_wav"), \
         patch.object(ctrl, "_transcribe", return_value="你好"), \
         patch.object(ctrl, "_stream_llm", return_value=reply_with_sep), \
         patch.object(ctrl._tts, "speak_streaming_end") as mock_end, \
         patch.object(ctrl._tts, "speak_streaming_start") as mock_start:
        ctrl._handle_utterance(b"audio")
    # LLM 结果按逐句协议分句显示（reply_show）
    assert shown, "未进入分句显示"
    # 一问一答写入会话
    assert ("user", "你好") in recorded
    assert any(r == "assistant" and "嗯，怎么了？" in c for r, c in recorded)
    # 不再自动流式口播
    mock_start.assert_not_called()
    mock_end.assert_not_called()
    # 分句态就绪：点击通过 advance_reply 播放
    assert ctrl._resp_cn and ctrl._resp_index == -1


def test_stream_llm_injects_phone_short_reply_prompt():
    ctrl = _make_controller()
    with patch("core.llm.backend_router.route_and_send", return_value=("reply", "chat")) as mock_route:
        assert ctrl._stream_llm("hello") == "reply"
    _, kwargs = mock_route.call_args
    inject = kwargs["inject_system_prompt"]
    assert "Phone mode reply policy" in inject
    assert "1-2 sentences" in inject
    assert kwargs["skip_history"] is True


def test_stream_llm_no_token_cap():
    """电话模式不设 max_tokens：推理模型思考即耗 token，上限会把正文整段
    掐掉 → 空回复（实测 mimo-v2.5 + 300 tokens 回复为空）。长度由 prompt 约束。"""
    ctrl = _make_controller()
    with patch("core.llm.backend_router.route_and_send", return_value=("reply", "chat")) as mock_route:
        ctrl._stream_llm("hello")
    _, kwargs = mock_route.call_args
    assert kwargs["response_max_tokens"] is None


def test_prepare_reply_japanese_only_falls_back_to_kana():
    """纯日语输出（无中文翻译段）时用日语假名句顶替字幕，避免空白。"""
    ctrl = _make_controller()
    shown: list[str] = []
    ctrl.reply_show.connect(lambda i, t, s: shown.append(s))
    ctrl._prepare_reply("（微笑んで）ええ、どうしたの？")
    assert shown and "ええ、どうしたの？" in shown[0]


# ===== 全双工（barge-in 打断 + turn 作废）=====

def _frame_of(rms_target: float, samples: int = 1024) -> "np.ndarray":
    import numpy as np
    noise = np.random.randn(samples).astype(np.float32)
    cur = float(np.sqrt(np.mean(noise ** 2)))
    return noise * (rms_target / cur)


def test_barge_in_interrupts_tts():
    """speaking 态用户大声说话（≥阈值×2.5 连续 2 帧）→ 停 TTS 转录用户。"""
    ctrl = _make_controller()
    ctrl._set_phase("speaking")
    barge_thresh = ctrl._vad.current_start_thresh * 2.5
    with patch.object(ctrl._tts, "stop") as mock_stop, \
         patch.object(ctrl, "_submit_user_audio") as mock_submit:
        # 连续 2 帧大声 → 触发打断（TTS 立刻停）
        ctrl._feed_barge_in(_frame_of(barge_thresh * 2), barge_thresh * 2)
        ctrl._feed_barge_in(_frame_of(barge_thresh * 2), barge_thresh * 2)
        assert mock_stop.called
        assert ctrl._barge_recording is True
        # 之后录到静音 12 帧提交
        for _ in range(12):
            ctrl._feed_barge_record(_frame_of(0.0001), 0.0001)
        assert mock_submit.called
        assert ctrl._barge_recording is False


def test_barge_in_ignores_tts_echo():
    """她说话的回声（电平低，< 阈值×2.5）不触发打断。"""
    ctrl = _make_controller()
    ctrl._set_phase("speaking")
    barge_thresh = ctrl._vad.current_start_thresh * 2.5
    with patch.object(ctrl._tts, "stop") as mock_stop:
        for _ in range(20):
            ctrl._feed_barge_in(_frame_of(barge_thresh * 0.5), barge_thresh * 0.5)
        assert not mock_stop.called
        assert ctrl._barge_recording is False


def test_turn_invalidation_drops_stale_reply():
    """改口/打断后（_turn_id 前进），旧回合的 LLM 回复不再播 TTS/改状态。"""
    ctrl = _make_controller()

    def fake_stream_llm(user_text):
        # 旧回合正在生成时，用户说了新话（turn 前进）
        ctrl._turn_id += 1
        return "（微笑）旧回复"
    with patch("core.voice.voice_call.encode_wav"), \
         patch.object(ctrl, "_transcribe", return_value="你好"), \
         patch.object(ctrl, "_stream_llm", side_effect=fake_stream_llm), \
         patch.object(ctrl._tts, "speak_with_options") as mock_speak, \
         patch.object(ctrl, "_set_phase") as mock_phase:
        ctrl._handle_utterance(_frame_of(0.01), turn_id=1)
    mock_speak.assert_not_called()  # 旧回复不播
    mock_phase.assert_not_called()  # 旧回合不改状态


def test_stale_delta_dropped():
    """旧回合的流式 delta 不送 TTS、不上字幕。"""
    ctrl = _make_controller()
    ctrl._active_turn = 1
    ctrl._turn_id = 2  # 回合已前进（被打断）
    with patch.object(ctrl._tts, "speak_streaming_start") as mock_start:
        ctrl._on_llm_delta("（微笑んで）ええ、どうしたの？")
    mock_start.assert_not_called()
    assert ctrl._streamed_reply == ""


# ===== 字幕只中文 / 语音只日语（逐句协议）=====

def test_subtitle_shows_chinese_only():
    """逐句字幕：中文字幕句不含日语原文（中日分离）。"""
    ctrl = _make_controller()
    shown: list[str] = []
    ctrl.reply_show.connect(lambda i, t, s: shown.append(s))
    ctrl._prepare_reply("[emotion:smile]（微笑んで）ええ、どうしたの？\n===\n（微笑）嗯，怎么了？")
    first = shown[0]
    assert "嗯，怎么了？" in first
    assert "ええ" not in first, f"字幕不应含日语: {first!r}"


def test_advance_tts_language_selects_voice():
    """逐句朗读：有日语句用日语 + 中文兜底；纯中文句仅显示不发音。"""
    # 中日交替 → 日语朗读 ja + 中文兜底 zh
    ctrl = _make_controller()
    shown: list[str] = []
    ctrl.reply_show.connect(lambda i, t, s: shown.append(s))
    ctrl._prepare_reply("（微笑）怎么了？\n===\n（微笑んで）どうしたの？")
    with patch.object(ctrl._tts, "speak_with_options") as mock_speak:
        ctrl.advance_reply()
    assert mock_speak.called
    args = mock_speak.call_args
    assert "どうしたの" in args.args[0]
    assert args.kwargs["text_lang"] == "ja"
    assert args.kwargs["fallback_lang"] == "zh"
    # 纯中文回复（无日语段）：仅逐句显示，不发音
    ctrl2 = _make_controller()
    shown2: list[str] = []
    ctrl2.reply_show.connect(lambda i, t, s: shown2.append(s))
    ctrl2._prepare_reply("没什么，随便聊聊")
    with patch.object(ctrl2._tts, "speak_with_options") as mock_speak2:
        ctrl2.advance_reply()
    assert not mock_speak2.called  # 无日语，不发音
    assert shown2, "中文句仍需逐句显示"
