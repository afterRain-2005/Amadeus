# tests/test_emotion_vocab.py — 情绪标签扩充（core/emotion_parser.py）
# 2026-08 对标 airi 情感表：5 → 10 种情绪。
import pytest

from core.emotion_parser import parse_reply


@pytest.mark.parametrize("emotion", [
    "neutral", "smile", "blush", "angry", "sad",
    "thinking", "surprised", "laugh", "sleepy", "confused",
])
def test_all_ten_emotions_parse(emotion):
    raw = f"[emotion:{emotion}]（动作）中文内容\n===\n（どうさ）にほんごないよう"
    parsed = parse_reply(raw)
    assert parsed.emotion == emotion
    assert "中文内容" in parsed.chinese
    assert "にほんごないよう" in parsed.japanese
    # 标签不应残留在正文里
    assert "[emotion:" not in parsed.chinese


def test_missing_tag_defaults_neutral():
    assert parse_reply("普通回复").emotion == "neutral"


def test_unknown_tag_defaults_neutral():
    assert parse_reply("[emotion:hype]内容").emotion == "neutral"


def test_new_motion_names_accepted_by_expression():
    """扩充情绪的默认动作映射应与 Live2D MOTIONS 词表一致。"""
    from core.companion.expression import EMOTION_DEFAULT_MOTION, VALID_EMOTIONS, VALID_MOTIONS
    assert {"surprised", "laugh", "sleepy", "confused"} <= VALID_EMOTIONS
    for emotion in ("surprised", "laugh", "sleepy", "confused"):
        assert EMOTION_DEFAULT_MOTION[emotion] in VALID_MOTIONS
