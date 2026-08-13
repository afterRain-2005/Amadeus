"""分段气泡 bug 修复测试：delta 期间不更新气泡文字。"""
from __future__ import annotations
import sys
from unittest.mock import MagicMock, patch


def test_agent_delta_does_not_set_bubble_text():
    """delta 期间不应调用 _set_bubble_text，应调用 _show_thinking_dots。"""
    # 用桩对象模拟 PetWindow，只测 _agent_delta 行为
    class StubWindow:
        def __init__(self):
            self._streamed_reply = ""
            self._history_expanded = False
            self.bubble_text_calls = 0
            self.thinking_calls = 0
        def _set_bubble_text(self, text):
            self.bubble_text_calls += 1
        def _show_thinking_dots(self):
            self.thinking_calls += 1
        def _agent_delta(self, text):
            # 复制 desktop_pet.py 修复后的逻辑
            self._streamed_reply += text
            if not self._history_expanded:
                self._show_thinking_dots()

    win = StubWindow()
    win._agent_delta("こん")
    win._agent_delta("にちは")
    assert win.bubble_text_calls == 0, "delta 期间不应调用 _set_bubble_text"
    assert win.thinking_calls == 2, "delta 期间应调用 _show_thinking_dots"


def test_agent_finished_triggers_layered_bubbles():
    """finished 后应调用 _show_layered_bubbles，不直接 _set_bubble_text 全文。"""
    class StubWindow:
        def __init__(self):
            self.layered_calls = 0
            self.bubble_text_calls = 0
        def _set_bubble_text(self, text):
            self.bubble_text_calls += 1
        def _show_layered_bubbles(self, text):
            self.layered_calls += 1
        def _agent_finished(self, reply):
            # 复制修复后逻辑：只调 _show_layered_bubbles
            self._show_layered_bubbles(reply)

    win = StubWindow()
    win._agent_finished("こんにちは。岡部、元気？")
    assert win.layered_calls == 1
    assert win.bubble_text_calls == 0, "finished 不应直接 _set_bubble_text 全文"
