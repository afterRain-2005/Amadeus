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


def test_dock_bar_icon_colors():
    """4 个普通图标为青色 #00d4ff，close 为红色 #ff3b30。"""
    for name in ["chat", "pin", "settings", "history"]:
        svg = ROOT / "resources" / "icons" / f"{name}.svg"
        assert "#00d4ff" in svg.read_text(encoding="utf-8"), f"{name} 应为青色"
    close = ROOT / "resources" / "icons" / "close.svg"
    assert "#ff3b30" in close.read_text(encoding="utf-8"), "close 应为红色"
