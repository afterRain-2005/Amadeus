# tests/test_voice_call_e2e.py
"""语音通话端到端链路测试：真实 VoiceCallController + 真实 SpeechPlayer 流水线。

只 mock 边界（STT / LLM 网络 / TTS 网络 / sounddevice），验证
「识别 → 流式 LLM delta → 流式 TTS → 播放队列 → 输出设备」整条链是否闭合。
"""
from unittest.mock import patch, MagicMock

from core.voice.voice_call import VoiceCallController


REPLY_FULL_JA_FIRST = (
    "[emotion:smile]（微笑んで）ええ、どうしたの？今日も頑張りましょ。\n===\n（微笑）怎么了？今天也要加油哦。"
)
REPLY_FULL_CN_FIRST = (
    "[emotion:smile]（微笑）今天也要加油哦。\n===\n（微笑んで）ええ、どうしたの？今日も頑張りましょ。"
)


def _fake_route_and_send(**kwargs):
    on_delta = kwargs["on_delta"]
    for chunk in [
        "[emotion:smile]（微笑んで）ええ、",
        "どうしたの？",
        "今日も頑張りましょ。",
        "\n===\n",
        "（微笑）怎么了？",
        "今天也要加油哦。",
    ]:
        on_delta(chunk)
    return REPLY_FULL_JA_FIRST, "chat"


def _fake_aliyun_stream(text, text_lang=None, session_id=None):
    # 0.2s @ 24kHz int16 静音 PCM × 3 块
    for _ in range(3):
        yield ("pcm", b"\x00\x00" * 4800)


def _run_pipeline(transcribe_text="你好"):
    ctrl = VoiceCallController({
        "endpoint": "https://api.deepseek.com/v1",
        "api_key": "sk-test",
        "model": "deepseek-chat",
    })
    subtitles: list[str] = []
    phases: list[str] = []
    ctrl.subtitle.connect(subtitles.append)
    ctrl.phase_changed.connect(phases.append)

    sd_out = MagicMock(name="sd.OutputStream")
    synth_mock = MagicMock(side_effect=_fake_aliyun_stream)
    with patch("core.voice.voice_call.encode_wav"), \
         patch.object(ctrl, "_transcribe", return_value=transcribe_text), \
         patch("core.llm.backend_router.route_and_send", side_effect=_fake_route_and_send), \
         patch.object(ctrl._tts, "_get_tts_provider", return_value="aliyun"), \
         patch.object(ctrl._tts, "_check_provider_available", return_value=True), \
         patch.object(ctrl._tts, "_synthesize_aliyun_stream", synth_mock), \
         patch("sounddevice.OutputStream", return_value=sd_out):
        ctrl._handle_utterance(b"audio")
        # 等 consumer/playback 线程收尾（consumer join playback 后才退出）
        if ctrl._tts._stream_thread is not None:
            ctrl._tts._stream_thread.join(timeout=10)
    return ctrl, subtitles, phases, sd_out, synth_mock


def test_pipeline_reaches_output_device():
    """整条链应闭合：TTS 启动 → PCM 到达输出设备。"""
    ctrl, subtitles, phases, sd_out, _ = _run_pipeline()
    assert ctrl._stream_tts_started is True
    assert sd_out.write.call_count > 0, "没有 PCM 写入输出设备（无声）"
    assert "speaking" in phases


def test_pipeline_shows_reply_as_subtitle():
    """通话中应把回复文本显示为字幕（Bug：回复从不显示）。"""
    ctrl, subtitles, phases, sd_out, _ = _run_pipeline()
    joined = "".join(subtitles)
    assert "ええ、どうしたの" in joined, f"日语台词未上字幕: {subtitles}"
    assert "加油" in joined, f"中文翻译未上字幕: {subtitles}"


def test_pipeline_first_sentence_sent_immediately():
    """电话模式首句即送合成：不等 14 字合并（缩短首声延迟）。"""
    ctrl, subtitles, phases, sd_out, synth_mock = _run_pipeline()
    # 假 stream 收到的每次调用对应一段送合成的文本；首句应已到达
    synth_calls = synth_mock.call_args_list
    assert synth_calls, "没有任何文本送入合成"
    first_text = synth_calls[0].args[0] if synth_calls[0].args else synth_calls[0].kwargs.get("text", "")
    assert "どうしたの" in first_text or "ええ" in first_text
