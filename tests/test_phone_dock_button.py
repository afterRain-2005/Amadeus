# tests/test_phone_dock_button.py
from unittest.mock import MagicMock, patch
import pytest


def test_call_toggle_decision_pure():
    """通话态切换决策是纯函数（参考 _decide_delta_action 模式）。"""
    from desktop_pet import _decide_call_toggle_action
    # 非通话态 → 进入通话
    assert _decide_call_toggle_action(in_call=False) == {"enter_call": True, "hangup": False}
    # 通话态 → 挂断
    assert _decide_call_toggle_action(in_call=True) == {"enter_call": False, "hangup": True}