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
    with patch("core.voice_call.encode_wav"), \
         patch("core.voice_call.describe_screen", return_value="VS Code 编辑") as mock_vis, \
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
    with patch("core.voice_call.encode_wav") as mock_encode, \
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
        # 首句立即送合成：first_merge_chars=1（电话模式延迟优先于吞吐）
        mock_start.assert_called_once_with(
            text_lang="ja", first_merge_chars=1, allow_fallback=True
        )
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
        mock_start.assert_called_once_with(
            text_lang="ja", first_merge_chars=1, allow_fallback=True
        )
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
    mock_end.assert_called_once_with(fallback_text="（歪头）嗯，怎么了？", fallback_lang="zh")
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
    assert args.kwargs["allow_fallback"] is True
    assert args.kwargs["fallback_text"] == "こんにちは"
    assert args.kwargs["fallback_lang"] == "ja"


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


def test_on_llm_delta_japanese_first_order():
    """电话模式新语序（日语在前、=== 后中文）：首个假名 delta 立即启动 TTS。

    日语先行让首句假名提前 ~9s 到达（不必等整段中文生成完），
    这是缩短首声延迟的关键；假名检测顺序无关。
    """
    ctrl = _make_controller()
    with patch.object(ctrl._tts, "speak_streaming_start") as mock_start, \
         patch.object(ctrl._tts, "speak_streaming_append") as mock_append:
        # 首个 delta 即日语（含假名）：立即启动 TTS
        ctrl._on_llm_delta("[emotion:smile]（微笑んで）ええ、どうしたの？")
        assert ctrl._stream_tts_started is True
        mock_start.assert_called_once_with(
            text_lang="ja", first_merge_chars=1, allow_fallback=True
        )
        assert mock_append.call_count == 1
        # 后续 === 与中文翻译：无假名，不追加
        ctrl._on_llm_delta("\n===\n（微笑）嗯，怎么了？")
        assert mock_append.call_count == 1


def test_on_llm_delta_emits_reply_subtitle():
    """流式回复实时上字幕（修复：通话中回复从不显示）。"""
    ctrl = _make_controller()
    subtitles: list[str] = []
    ctrl.subtitle.connect(subtitles.append)
    with patch.object(ctrl._tts, "speak_streaming_start"), \
         patch.object(ctrl._tts, "speak_streaming_append"):
        ctrl._on_llm_delta("[emotion:smile]（微笑んで）ええ、どうしたの？")
        ctrl._on_llm_delta("\n===\n（微笑）嗯，怎么了？")
    joined = "".join(subtitles)
    assert "ええ、どうしたの？" in joined, f"日语台词未上字幕: {subtitles}"
    assert "嗯，怎么了？" in joined, f"中文翻译未上字幕: {subtitles}"


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
    with patch("core.voice_call.encode_wav"), \
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


# ===== 字幕只中文 / 语音只日语 =====

def test_subtitle_shows_chinese_only():
    """字幕只出中文（需求）；纯日语无翻译时回退显示日语。"""
    ctrl = _make_controller()
    subs = []
    ctrl.subtitle.connect(subs.append)
    ctrl._active_turn = ctrl._turn_id
    with patch.object(ctrl._tts, "speak_streaming_start"), \
         patch.object(ctrl._tts, "speak_streaming_append"):
        ctrl._on_llm_delta("[emotion:smile]（微笑んで）ええ、どうしたの？")
        ctrl._on_llm_delta("\n===\n（微笑）嗯，怎么了？")
    joined = "".join(subs)
    assert "嗯，怎么了？" in joined
    # 最终字幕只出中文（首 delta 无翻译时回退日语属预期中间态）
    assert "ええ" not in subs[-1], f"最终字幕不应含日语: {subs[-1]!r}"


def test_fallback_tts_japanese_with_chinese_voice():
    """兜底路径：有日语段用日语合成；纯中文回复用中文腔读（不混语言）。"""
    ctrl = _make_controller()
    with patch("core.voice_call.encode_wav"), \
         patch.object(ctrl, "_transcribe", return_value="你好"), \
         patch.object(ctrl, "_stream_llm", return_value="[emotion:smile]（微笑）怎么了？\n===\n（微笑んで）どうしたの？"), \
         patch.object(ctrl._tts, "speak_with_options") as mock_speak:
        ctrl._handle_utterance(_frame_of(0.01))
    args = mock_speak.call_args
    assert "どうしたの" in args.args[0]
    assert args.kwargs["text_lang"] == "ja"

    # 纯中文回复：中文腔读
    ctrl2 = _make_controller()
    with patch("core.voice_call.encode_wav"), \
         patch.object(ctrl2, "_transcribe", return_value="你好"), \
         patch.object(ctrl2, "_stream_llm", return_value="没什么，随便聊聊"), \
         patch.object(ctrl2._tts, "speak_with_options") as mock_speak2:
        ctrl2._handle_utterance(_frame_of(0.01))
    args2 = mock_speak2.call_args
    assert args2.kwargs["text_lang"] == "zh"
