"""DockBar 单元测试：5 按钮、SVG 加载、颜色。"""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dock_bar_has_5_icons():
    """DockBar 应有 5 个图标文件（对话/固定/设置/记录/退出）。"""
    icons = ["chat", "pin", "settings", "history", "close"]
    for name in icons:
        svg = ROOT / "resources" / "icons" / f"{name}.svg"
        assert svg.exists(), f"缺少图标 {svg}"
        assert svg.read_text(encoding="utf-8").startswith("<svg")


def test_dock_bar_terminal_icon():
    """终端按钮使用独立 terminal 图标（rose 语义修正）。"""
    svg = ROOT / "resources" / "icons" / "terminal.svg"
    assert svg.exists(), f"缺少图标 {svg}"
    assert svg.read_text(encoding="utf-8").startswith("<svg")


def test_dock_bar_icon_colors():
    """4 个普通图标为米黄 #c1b492，close 为玫瑰 #d2738a（fauux 配色）。"""
    for name in ["chat", "pin", "settings", "history"]:
        svg = ROOT / "resources" / "icons" / f"{name}.svg"
        assert "#c1b492" in svg.read_text(encoding="utf-8"), f"{name} 应为米黄"
    close = ROOT / "resources" / "icons" / "close.svg"
    assert "#d2738a" in close.read_text(encoding="utf-8"), "close 应为玫瑰"
