"""分段气泡 bug 回归测试：直接导入 desktop_pet 模块级纯函数验证决策逻辑。

旧版测试把 _agent_delta 的逻辑复制进 StubWindow，导致 desktop_pet.py 改动后测试仍恒通过，
无法守护真实代码。本测试改为导入 desktop_pet._decide_delta_action / _streamed_display_text，
覆盖 delta 阶段的流式气泡上屏 / 思考点展示 / 终端模式抑制三类行为。

v4.1 起 delta 决策变更（响应提速）：气泡模式下只要有可显示内容（_streamed_display_text
非空）即流式更新气泡文字并停止思考点动画；终端模式两者都抑制（终端回显已替代）。
"""
from __future__ import annotations

import desktop_pet


# ===== _streamed_display_text：流式显示文本提取 =====


def test_streamed_display_text_plain_chinese_passthrough():
    """纯中文原样返回（流式上屏的主体路径）。"""
    assert desktop_pet._streamed_display_text("你好，冈部。") == "你好，冈部。"


def test_streamed_display_text_strips_full_emotion_tag():
    """完整 [emotion:xxx] 前缀标签去掉，正文保留。"""
    assert desktop_pet._streamed_display_text("[emotion:blush]突然说什么啊") == "突然说什么啊"


def test_streamed_display_text_strips_partial_emotion_tag():
    """标签只到达一半（如 "[emotion:smi"）也不闪现在气泡里。"""
    assert desktop_pet._streamed_display_text("[emotion:smi") == ""
    assert desktop_pet._streamed_display_text("[") == ""


def test_streamed_display_text_cuts_at_full_separator():
    """=== 之后的日语段不进入显示文本（中文在 === 之前）。"""
    streamed = "突然说什么啊，笨蛋。\n===\n急に何言ってるのよ、バカ。"
    assert desktop_pet._streamed_display_text(streamed) == "突然说什么啊，笨蛋。"


def test_streamed_display_text_cuts_at_partial_separator():
    """分隔符只到达一半（"=="）也按分隔处理，避免等号残影。"""
    assert desktop_pet._streamed_display_text("你好==") == "你好"


def test_streamed_display_text_empty_input():
    """空流式输入返回空串（尚无可显示内容）。"""
    assert desktop_pet._streamed_display_text("") == ""
    assert desktop_pet._streamed_display_text("   ") == ""


# ===== _decide_delta_action：delta 阶段决策 =====


def test_delta_streams_bubble_text_once_content_visible():
    """suppress_thinking=False 且有可见中文：流式更新气泡文字、停止思考点。"""
    _, show_thinking, set_bubble = desktop_pet._decide_delta_action(
        "", "こん", suppress_thinking=False
    )
    assert set_bubble is True
    assert show_thinking is False


def test_delta_accumulates_streamed_reply_across_calls():
    """连续多次 delta 应正确累积 streamed_reply（模拟流式 token 到达）。"""
    streamed = ""
    for chunk in ("こん", "にちは", "。", "岡部", "、", "元気？"):
        streamed, _, _ = desktop_pet._decide_delta_action(
            streamed, chunk, suppress_thinking=False
        )
    assert streamed == "こんにちは。岡部、元気？"


def test_delta_keeps_thinking_while_only_tag_prefix_arrived():
    """只到达 [emotion: 前缀（无正文）时：继续思考点动画，不上屏。"""
    for streamed, chunk in (("", "[emotion:smi"), ("[emotion:smi", "le]")):
        new_streamed, show_thinking, set_bubble = desktop_pet._decide_delta_action(
            streamed, chunk, suppress_thinking=False
        )
        assert set_bubble is False
        assert show_thinking is True


def test_delta_suppresses_everything_in_terminal_mode():
    """终端模式（suppress_thinking=True）：思考点与气泡更新都抑制（终端回显已替代）。"""
    _, show_thinking, set_bubble = desktop_pet._decide_delta_action(
        "", "你好，冈部。", suppress_thinking=True
    )
    assert show_thinking is False
    assert set_bubble is False


def test_delta_decision_table_by_visibility():
    """决策表守护：气泡模式以 _streamed_display_text 非空为准切换上屏。"""
    cases = [
        ("", "你好", True),                # 有正文 → 上屏
        ("[emotion:smile]", "你好", True), # 已有标签，正文到达 → 上屏
        ("[emotion:smi", "le]", False),    # 标签补完仍无正文 → 思考点
        ("你好", "===\nこんにちは", True), # === 后日语增量，中文已可见 → 上屏
    ]
    for streamed, chunk, expect_set in cases:
        _, show_thinking, set_bubble = desktop_pet._decide_delta_action(
            streamed, chunk, suppress_thinking=False
        )
        assert set_bubble is expect_set, f"streamed={streamed!r} chunk={chunk!r}"
        assert show_thinking is (not expect_set), f"streamed={streamed!r} chunk={chunk!r}"
