"""分段气泡 bug 回归测试：直接导入 desktop_pet 模块级纯函数验证决策逻辑。

旧版测试把 _agent_delta 的逻辑复制进 StubWindow，导致 desktop_pet.py 改动后测试仍恒通过，
无法守护真实代码。本测试改为导入 desktop_pet._decide_delta_action，覆盖 delta 阶段的
思考点展示/气泡文字抑制/终端模式思考点抑制三类行为。
"""
from __future__ import annotations

import desktop_pet


def test_delta_does_not_set_bubble_text_when_not_suppressed():
    """suppress_thinking=False 时：累积文字、显示思考点、不更新气泡文字。"""
    streamed, show_thinking, set_bubble = desktop_pet._decide_delta_action(
        "", "こん", suppress_thinking=False
    )
    assert streamed == "こん"
    assert show_thinking is True
    assert set_bubble is False


def test_delta_accumulates_streamed_reply_across_calls():
    """连续多次 delta 应正确累积 streamed_reply（模拟流式 token 到达）。"""
    streamed = ""
    for chunk in ("こん", "にちは", "。", "岡部", "、", "元気？"):
        streamed, _, _ = desktop_pet._decide_delta_action(
            streamed, chunk, suppress_thinking=False
        )
    assert streamed == "こんにちは。岡部、元気？"


def test_delta_suppresses_thinking_dots_when_suppressed():
    """suppress_thinking=True 时不应显示思考点（终端流式回显已替代）。"""
    _, show_thinking, set_bubble = desktop_pet._decide_delta_action(
        "こん", "にちは", suppress_thinking=True
    )
    assert show_thinking is False
    # delta 期间无论是否抑制思考点都不应直接更新气泡文字
    assert set_bubble is False


def test_delta_bubble_text_never_set_regardless_of_suppression():
    """决策表守护：delta 期间 should_set_bubble_text 在所有 suppress_thinking 取值下恒为 False。"""
    for suppress_thinking in (False, True):
        _, _, set_bubble = desktop_pet._decide_delta_action(
            "", "chunk", suppress_thinking=suppress_thinking
        )
        assert set_bubble is False, (
            f"delta 期间不应调用 _set_bubble_text（suppress_thinking={suppress_thinking}）"
        )
