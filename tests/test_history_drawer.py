"""HistoryDrawer 单元测试：青灰条消息样式。"""
from __future__ import annotations


def test_history_html_kurisu_style():
    """Kurisu 消息应有 cyan-soft 背景 + cyan 左边条。"""
    from desktop_pet import _build_kurisu_html
    html = _build_kurisu_html("こんにちは")
    assert "rgba(0,212,255,0.16)" in html
    assert "border-left:2px solid #00d4ff" in html
    assert "こんにちは" in html


def test_history_html_you_style():
    """You 消息应有灰背景 + 右灰边条。"""
    from desktop_pet import _build_you_html
    html = _build_you_html("你好")
    assert "rgba(255,255,255,0.06)" in html
    assert "border-right:2px solid #8e8e93" in html
    assert "你好" in html


def test_history_html_escapes_html():
    """消息中的 HTML 特殊字符应被转义。"""
    from desktop_pet import _build_kurisu_html
    html = _build_kurisu_html("<script>x</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
