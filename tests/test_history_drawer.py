"""HistoryDrawer 单元测试：fauux 玫瑰/米黄条消息样式。"""
from __future__ import annotations


def test_history_html_kurisu_style():
    """Kurisu 消息应有玫瑰软底 + 玫瑰左边条（fauux）。"""
    from desktop_pet import _build_kurisu_html
    html = _build_kurisu_html("こんにちは")
    assert "rgba(210,115,138,0.22)" in html
    assert "border-left:2px solid #d2738a" in html
    assert "こんにちは" in html


def test_history_html_you_style():
    """You 消息应有淡玫瑰底 + 右米黄边条（fauux）。"""
    from desktop_pet import _build_you_html
    html = _build_you_html("你好")
    assert "rgba(210,115,138,0.14)" in html
    assert "border-right:2px solid #8a7f63" in html
    assert "你好" in html


def test_history_html_escapes_html():
    """消息中的 HTML 特殊字符应被转义。"""
    from desktop_pet import _build_kurisu_html
    html = _build_kurisu_html("<script>x</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
