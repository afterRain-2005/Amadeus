from core.companion.call_style import build_phone_short_reply_prompt


def test_phone_short_reply_prompt_keeps_call_constraints():
    prompt = build_phone_short_reply_prompt()
    assert "Phone mode reply policy" in prompt
    assert "1-2 sentences" in prompt
    assert "[emotion:" in prompt
    assert "===" in prompt
    assert "TTS" in prompt


def test_phone_prompt_requires_japanese_only():
    """电话模式只输出日语单段：流式 TTS 只消费日语段，双语格式下首个假名
    delta 要等整段中文生成完才到达（实测 +9s），且短 max_tokens 会把日语段
    整段截掉 → TTS 永不启动 → 无声。"""
    prompt = build_phone_short_reply_prompt()
    assert "OVERRIDES" in prompt
    assert "JAPANESE ONLY" in prompt
    assert "Do NOT output ===" in prompt
    assert "Chinese translation" in prompt
