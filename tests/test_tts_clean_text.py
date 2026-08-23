"""Tests for SpeechPlayer._clean_tts_text: 修复 CosyVoice 末尾拖音问题。

移植自 amadeus src/lib/tts.ts:305-322 cleanTTS_text：
1. 省略号 → 单句号
2. 波浪号 → 移除
3. 多余空白 → 单空格
4. 末尾确保有句号
"""
from core.voice.tts_client import SpeechPlayer


def _clean(text: str) -> str:
    player = SpeechPlayer()
    return player._clean_tts_text(text)


def test_empty_returns_empty():
    assert _clean("") == ""


def test_none_safe():
    assert _clean(None) == ""  # type: ignore[arg-type]


def test_ellipsis_replaced_with_period():
    """中文省略号 … 替换为单句号（CosyVoice 不会把…读成"yi"拖音）。"""
    assert _clean("ええ…どうしたの") == "ええ。どうしたの。"


def test_multiple_periods_replaced_with_single_period():
    """连续句点 ... 替换为单句号。"""
    assert _clean("ええ...どうしたの") == "ええ。どうしたの。"


def test_consecutive_periods_collapsed():
    """连续多个。合并为一个。"""
    assert _clean("ええ。。どうしたの") == "ええ。どうしたの。"


def test_tilde_removed():
    """波浪号 ~ 和 〜 移除（CosyVoice 会读成颤音）。"""
    assert _clean("ええ〜どうしたの") == "ええどうしたの。"
    assert _clean("ええ~どうしたの") == "ええどうしたの。"


def test_whitespace_collapsed():
    """多个空格/换行合并为单空格。"""
    assert _clean("ええ\n\n  どうしたの") == "ええ どうしたの。"


def test_adds_period_if_missing():
    """末尾无标点自动加句号（让 CosyVoice 明确句子结束）。"""
    assert _clean("ええ、どうしたの") == "ええ、どうしたの。"


def test_no_double_period_if_already_ends_with_punctuation():
    """末尾已有句号/感叹号/问号时不再加句号。"""
    assert _clean("ええ、どうしたの。") == "ええ、どうしたの。"
    assert _clean("ええ！") == "ええ！"
    assert _clean("どうしたの？") == "どうしたの？"


def test_combined_fixes():
    """组合用例：省略号 + 波浪号 + 多空格 + 无句号。"""
    text = "ええ〜...  どうしたの"
    assert _clean(text) == "ええ。 どうしたの。"


def test_japanese_with_emoji_preserved():
    """emoji 和正常日文字符保持原样。"""
    text = "ええ、どうしたの😊"
    # emoji 不在末尾标点正则中，会被加句号
    assert _clean(text) == "ええ、どうしたの😊。"


def test_mixed_chinese_japanese():
    """中日混合文本（GPT-SoVITS 输出场景）。"""
    text = "你好...ええ~どうしたの"
    assert _clean(text) == "你好。ええどうしたの。"
