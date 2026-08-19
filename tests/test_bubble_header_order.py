# tests/test_bubble_header_order.py
"""回归：PetWindow.__init__ 在创建 bubble_header/bubble_footer 之前可能先行调用
_set_bubble_text（本地存在历史会话时），旧代码无条件引用两个属性导致
AttributeError，exe/start.bat 启动即静默崩溃。

修复：几何同步抽为模块级纯函数 _sync_bubble_accessories，两个配件尚不存在时
（传 None）跳过同步；PetWindow 位于 run_overlay 函数内部无法直接实例化，
测试守护真实函数而非复制逻辑（同 test_bubble_animation 先例）。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QLabel

import desktop_pet


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_sync_skipped_when_accessories_missing(qapp):
    """崩溃路径等价：配件未创建（None）时调用，不抛异常、无副作用。"""
    desktop_pet._sync_bubble_accessories(None, None, 10, 100, 40)


def test_sync_updates_header_footer_geometry(qapp):
    """正常路径：配件存在时几何随气泡同步，语义不变。"""
    header = QLabel("K U R I S U")
    footer = QLabel("wire ESTABLISHED")
    desktop_pet._sync_bubble_accessories(header, footer, 10, 100, 40)
    assert header.geometry() == QRect(10, 8, 100, 12)
    assert footer.geometry() == QRect(10, 40 + 6 - 14, 100, 12)


def test_sync_does_not_touch_missing_side(qapp):
    """仅一侧存在（早期半构建状态）同样安全：不报错、已存在侧不被意外修改。"""
    footer = QLabel("wire ESTABLISHED")
    before = footer.geometry()
    desktop_pet._sync_bubble_accessories(None, footer, 10, 100, 40)
    assert footer.geometry() == before