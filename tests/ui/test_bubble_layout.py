# tests/test_bubble_layout.py
"""v4 气泡富文本布局纯函数测试：_wrap_bubble_html（1.5 行距/左对齐/转义）
与 _bubble_size_hint（QTextDocument 尺寸估算，QLabel 无法用 QFontMetrics
测富文本行距）。守护 desktop_pet.py 模块级真实函数。"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

import desktop_pet


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_wrap_bubble_html_escapes_and_breaks(qapp):
    """HTML 特殊字符转义 + 换行转 <br>。"""
    html = desktop_pet._wrap_bubble_html("<a>\n&")
    assert "&lt;a&gt;" in html
    assert "<br>" in html


def test_wrap_bubble_html_line_height_and_align(qapp):
    """v4：1.5 行距 + 左对齐应写入富文本样式。"""
    html = desktop_pet._wrap_bubble_html("こんにちは")
    assert "line-height:150%" in html
    assert "text-align:left" in html


def test_wrap_bubble_html_plain_no_break(qapp):
    """单行纯文本不应插入 <br>。"""
    html = desktop_pet._wrap_bubble_html("こんにちは")
    assert "<br>" not in html


def test_size_hint_grows_with_lines(qapp):
    """行数越多高度越大，宽度不超过上限。"""
    font = QFont("Consolas")
    font.setPixelSize(14)
    one = desktop_pet._bubble_size_hint(
        desktop_pet._wrap_bubble_html("短"), font, 340
    )
    many = desktop_pet._bubble_size_hint(
        desktop_pet._wrap_bubble_html("第一行\n第二行\n第三行"), font, 340
    )
    assert one[1] < many[1]
    assert one[0] <= 340 and many[0] <= 340


def test_size_hint_wraps_long_text(qapp):
    """超长文本应在 340 宽度内换行（高度增加，不溢出宽度）。"""
    font = QFont("Consolas")
    font.setPixelSize(14)
    w, h = desktop_pet._bubble_size_hint(
        desktop_pet._wrap_bubble_html("很长" * 200), font, 340
    )
    assert w <= 340
    assert h > 36