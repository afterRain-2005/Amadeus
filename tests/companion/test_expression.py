"""expression 模块测试：规则解析（中/日动作括号）+ Ollama 分类 + 失败回退。"""
import json
from unittest.mock import patch

import pytest

from core.companion.expression import (
    ACTION_MOTION_MAP,
    ParsedExpression,
    classify_expression,
    decide_expression,
    parse_expression,
)


# === parse_expression：emotion 标签 ===

def test_emotion_tag_extracted():
    expr = parse_expression("[emotion:blush]（别过脸）...突然说什么啊，笨蛋。\n===\n（顔をそらす）...急に何言ってるのよ、バカ。")
    assert expr.emotion == "blush"
    assert expr.motion == "blush"


def test_emotion_tag_missing_defaults_neutral():
    expr = parse_expression("（歪头）嗯，怎么了？")
    assert expr.emotion == "neutral"
    assert expr.motion == "neutral"


def test_emotion_tag_invalid_falls_back_neutral():
    expr = parse_expression("[emotion:rage]（前倾）你给我等着！")
    assert expr.emotion == "neutral"
    assert expr.motion == "angry"  # 动作括号仍生效


def test_empty_reply():
    expr = parse_expression("")
    assert expr == ParsedExpression()
    expr = parse_expression(None)  # type: ignore[arg-type]
    assert expr == ParsedExpression()


# === parse_expression：动作括号 ===

def test_action_bracket_chinese():
    assert parse_expression("[emotion:neutral]（叉腰）我才没有在等你。").motion == "hands_on_hips"
    assert parse_expression("[emotion:smile]（抱胸）哼，随你怎么说。").motion == "arms_crossed"
    assert parse_expression("[emotion:angry]（扶额）你真是无药可救了。").motion == "facepalm"
    assert parse_expression("[emotion:neutral]（摊手）那我也没办法了。").motion == "shrug"
    assert parse_expression("[emotion:thinking]（托腮）让我想想...").motion == "chin_rest"


def test_action_bracket_japanese():
    assert parse_expression("[emotion:neutral]（手を腰に）別に待ってないわ。").motion == "hands_on_hips"
    assert parse_expression("[emotion:smile]（腕を組む）ふん、好きにすれば。").motion == "arms_crossed"
    assert parse_expression("[emotion:angry]（額に手を当て）本当に救いようがないわね。").motion == "facepalm"
    assert parse_expression("[emotion:neutral]（肩をすくめる）それじゃ仕方ないわ。").motion == "shrug"
    assert parse_expression("[emotion:thinking]（頬杖をつく）考えさせて。").motion == "chin_rest"


def test_action_bracket_no_match_uses_emotion_default():
    expr = parse_expression("[emotion:sad]（望着窗外）今天又下雨了。")
    assert expr.motion == "sad"  # 无词表命中 → 情绪默认动作


def test_action_bracket_halfwidth_parens():
    assert parse_expression("[emotion:neutral](歪头)嗯？").motion == "neutral"


def test_action_map_keywords_covered_by_valid_motions():
    """词表所有映射值都必须落在合法 motion 集合内。"""
    valid = {
        "neutral", "smile", "blush", "angry", "sad", "thinking",
        "hands_on_hips", "arms_crossed", "facepalm", "shrug", "chin_rest",
    }
    for _keyword, motion in ACTION_MOTION_MAP:
        assert motion in valid, f"非法 motion: {motion}"


# === classify_expression：Ollama 分类 ===

def test_classify_success():
    payload = {"message": {"content": json.dumps(
        {"emotion": "angry", "motion": "arms_crossed"}, ensure_ascii=False)}}
    with patch("core.companion.expression.httpx.Client") as mock_cls:
        resp = mock_cls.return_value.__enter__.return_value.post.return_value
        resp.is_error = False
        resp.json.return_value = payload
        expr = classify_expression("[emotion:angry]哼！", base_url="http://x:1", model="m")
    assert expr is not None
    assert expr.emotion == "angry"
    assert expr.motion == "arms_crossed"


def test_classify_http_error_returns_none():
    with patch("core.companion.expression.httpx.Client") as mock_cls:
        resp = mock_cls.return_value.__enter__.return_value.post.return_value
        resp.is_error = True
        expr = classify_expression("测试", base_url="http://x:1", model="m")
    assert expr is None


def test_classify_exception_returns_none():
    with patch("core.companion.expression.httpx.Client") as mock_cls:
        mock_cls.side_effect = Exception("connection refused")
        expr = classify_expression("测试", base_url="http://x:1", model="m")
    assert expr is None


def test_classify_invalid_json_returns_none():
    with patch("core.companion.expression.httpx.Client") as mock_cls:
        resp = mock_cls.return_value.__enter__.return_value.post.return_value
        resp.is_error = False
        resp.json.return_value = {"message": {"content": "不是JSON"}}
        expr = classify_expression("测试", base_url="http://x:1", model="m")
    assert expr is None


def test_classify_invalid_labels_sanitized():
    payload = {"message": {"content": json.dumps(
        {"emotion": "rage", "motion": "fly"}, ensure_ascii=False)}}
    with patch("core.companion.expression.httpx.Client") as mock_cls:
        resp = mock_cls.return_value.__enter__.return_value.post.return_value
        resp.is_error = False
        resp.json.return_value = payload
        expr = classify_expression("测试", base_url="http://x:1", model="m")
    assert expr is not None
    assert expr.emotion == "neutral"  # 非法情绪 → neutral
    assert expr.motion == ""  # 非法动作 → 空（caller 决定回退）


def test_classify_empty_text_returns_none():
    assert classify_expression("", base_url="http://x:1", model="m") is None
    assert classify_expression("   ", base_url="http://x:1", model="m") is None


# === decide_expression：综合判定与回退 ===

def test_decide_with_ollama_uses_classified():
    with patch("core.companion.expression.classify_expression") as mock_cls:
        mock_cls.return_value = ParsedExpression(emotion="smile", motion="chin_rest")
        expr = decide_expression(
            "[emotion:sad]（低头）...",
            ollama={"base_url": "http://127.0.0.1:11434", "model": "qwen2.5:0.5b"},
        )
    mock_cls.assert_called_once()
    assert expr.emotion == "smile"
    assert expr.motion == "chin_rest"


def test_decide_ollama_failure_falls_back_rules():
    with patch("core.companion.expression.classify_expression") as mock_cls:
        mock_cls.return_value = None  # Ollama 不可达/超时
        expr = decide_expression(
            "[emotion:angry]（叉腰）你这个笨蛋！",
            ollama={"base_url": "http://127.0.0.1:11434", "model": "qwen2.5:0.5b"},
        )
    assert expr.emotion == "angry"
    assert expr.motion == "hands_on_hips"


def test_decide_ollama_classified_motion_empty_falls_back():
    """小模型返回了 emotion 但 motion 非法（空）→ 回退规则解析。"""
    with patch("core.companion.expression.classify_expression") as mock_cls:
        mock_cls.return_value = ParsedExpression(emotion="neutral", motion="")
        expr = decide_expression(
            "[emotion:blush]（别过脸）笨蛋...",
            ollama={"base_url": "http://x", "model": "m"},
        )
    assert expr.motion == "blush"  # 规则解析兜底


def test_decide_without_ollama_uses_rules():
    assert decide_expression("[emotion:neutral]（托腮）嗯。", ollama=None).motion == "chin_rest"
    assert decide_expression("[emotion:neutral]（托腮）嗯。", ollama={}).motion == "chin_rest"
    assert decide_expression("[emotion:neutral]（托腮）嗯。", ollama={"base_url": "", "model": ""}).motion == "chin_rest"


def test_decide_with_ollama_calls_classifier_with_timeout():
    with patch("core.companion.expression.classify_expression") as mock_cls:
        mock_cls.return_value = ParsedExpression(emotion="neutral", motion="neutral")
        decide_expression(
            "文本", ollama={"base_url": "http://x", "model": "m", "timeout": 7}
        )
        _call = mock_cls.call_args
        assert _call.kwargs["timeout"] == 7.0