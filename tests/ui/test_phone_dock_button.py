# tests/test_phone_dock_button.py
from unittest.mock import MagicMock, patch
from pathlib import Path
import pytest


def test_call_toggle_decision_pure():
    """通话态切换决策是纯函数（参考 _decide_delta_action 模式）。"""
    from desktop_pet import _decide_call_toggle_action
    # 非通话态 → 进入通话
    assert _decide_call_toggle_action(in_call=False) == {"enter_call": True, "hangup": False}
    # 通话态 → 挂断
    assert _decide_call_toggle_action(in_call=True) == {"enter_call": False, "hangup": True}


def test_companion_canvas_preserves_live2d_geometry():
    from desktop_pet import (
        CANVAS_WINDOW_H,
        CANVAS_WINDOW_W,
        CHARACTER_VIEW_H,
        CHARACTER_VIEW_W,
        CHARACTER_VIEW_X,
        CHARACTER_VIEW_Y,
    )

    assert (CANVAS_WINDOW_W, CANVAS_WINDOW_H) == (304, 585)
    assert (CHARACTER_VIEW_X, CHARACTER_VIEW_Y, CHARACTER_VIEW_W, CHARACTER_VIEW_H) == (20, 50, 264, 496)


def test_companion_canvas_drops_phone_hardware_metaphors():
    root = Path(__file__).resolve().parents[2]
    page = (root / "live2d" / "phone_live2d_page.html").read_text(encoding="utf-8")

    assert 'class="companion-page"' in page
    assert 'class="terminal-node"' not in page
    assert "homeBtn" not in page
    assert 'class="speaker"' not in page
    assert 'class="side ' not in page


def test_live2d_page_contains_no_window_ui():
    root = Path(__file__).resolve().parents[2]
    page = (root / "live2d" / "phone_live2d_page.html").read_text(encoding="utf-8")

    assert "html2canvas" not in page
    assert "canvasMinimize" not in page
    assert "page-commandbar" not in page
    assert "resources/bg.png" not in page
    assert "const VIEW_X = 20" in page
    assert "const VIEW_Y = 50" in page
    assert "const VIEW_W = 264" in page
    assert "const VIEW_H = 496" in page
