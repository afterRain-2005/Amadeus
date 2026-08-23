# tests/test_tts_stream_merge.py
"""SpeechPlayer 流式首句合并阈值测试：first_merge_chars 会话级覆盖。"""
from core.voice.tts_client import SpeechPlayer


def _drain(q):
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


def test_default_first_sentence_waits_for_threshold():
    """默认（桌面模式）：首句攒到 _MERGE_THRESHOLD=14 字才送合成队列。"""
    p = SpeechPlayer()
    p.speak_streaming_start(text_lang="ja")
    p.speak_streaming_append("こんにちは。")  # 5 字 < 14
    assert _drain(p._stream_queue) == []
    p.speak_streaming_append("Amadeusです。")  # 累计 15 字 ≥ 14
    sent = _drain(p._stream_queue)
    assert len(sent) == 1
    assert "こんにちは" in sent[0][0]
    p.stop()


def test_first_merge_chars_immediate():
    """电话模式（first_merge_chars=1）：首句切出句末标点即送，不等合并。"""
    p = SpeechPlayer()
    p.speak_streaming_start(text_lang="ja", first_merge_chars=1)
    p.speak_streaming_append("ええ、どうしたの？")  # 8 字 < 14，但应立即送
    sent = _drain(p._stream_queue)
    assert len(sent) == 1
    assert "ええ、どうしたの" in sent[0][0]
    # 后续句仍按 _MERGE_UPPER 合并（吞吐优先）
    p.speak_streaming_append("今日も頑張りましょ。")
    assert _drain(p._stream_queue) == []
    p.stop()


def test_first_merge_chars_not_leak_across_sessions():
    """会话级覆盖不泄漏：新会话不传参数恢复默认 14 字阈值。"""
    p = SpeechPlayer()
    p.speak_streaming_start(text_lang="ja", first_merge_chars=1)
    p.speak_streaming_append("ええ、どうしたの？")
    assert len(_drain(p._stream_queue)) == 1
    p.stop()
    p.speak_streaming_start(text_lang="ja")
    p.speak_streaming_append("こんにちは。")  # 5 字，默认阈值下应等待
    assert _drain(p._stream_queue) == []
    p.stop()
