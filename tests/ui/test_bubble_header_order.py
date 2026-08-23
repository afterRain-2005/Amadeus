# tests/test_bubble_header_order.py
"""回归：PetWindow.__init__ 在创建 bubble_header/bubble_footer 之前可能先行调用
_set_bubble_text（本地存在历史会话时），旧代码无条件引用两个属性导致
AttributeError，exe/start.bat 启动即静默崩溃。

修复：几何同步抽为模块级纯函数 _sync_bubble_accessories，配件尚不存在时
（传 None）跳过同步；PetWindow 位于 run_overlay 函数内部无法直接实例化，
测试守护真实函数而非复制逻辑（同 test_bubble_animation 先例）。

v4：签名扩展为 (header, footer, corners, status_line, x, y, w, h)：
- 名牌/注脚改为一体标签（贴合气泡上下缘，居中收窄）
- 新增四角括号 ⌈⌉⌊⌋ 与状态行同步
- y 为气泡纵坐标（气泡下移至 Dock 栏上方后不再固定于顶部）"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QLabel

import desktop_pet


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _corners(qapp):
    labels = []
    for ch in ("⌈", "⌉", "⌊", "⌋"):
        labels.append(QLabel(ch))
    return labels


def test_sync_skipped_when_accessories_missing(qapp):
    """崩溃路径等价：配件未创建（None）时调用，不抛异常、无副作用。"""
    desktop_pet._sync_bubble_accessories(None, None, None, None, 10, 400, 100, 40)


def test_sync_updates_header_footer_geometry(qapp):
    """正常路径：配件存在时几何随气泡同步，名牌/注脚收窄居中贴合上下缘。"""
    header = QLabel("K U R I S U")
    footer = QLabel("wire ESTABLISHED")
    desktop_pet._sync_bubble_accessories(header, footer, None, None, 10, 400, 100, 40)
    assert header.geometry() == QRect(10, 398, 100, 16)
    assert footer.geometry() == QRect(10, 424, 100, 16)


def test_sync_caps_header_width(qapp):
    """名牌宽度上限 150，超出时居中收窄（不整行铺满气泡）。"""
    header = QLabel("K U R I S U")
    desktop_pet._sync_bubble_accessories(header, None, None, None, 0, 400, 300, 80)
    assert header.geometry() == QRect(75, 398, 150, 16)


def test_sync_corners_track_bubble_edges(qapp):
    """四角括号应贴在气泡四角（外扩 5px，随几何变化）。"""
    corners = _corners(qapp)
    desktop_pet._sync_bubble_accessories(None, None, corners, None, 10, 400, 200, 60)
    assert corners[0].geometry() == QRect(5, 398, 12, 12)     # ⌈ 左上
    assert corners[1].geometry() == QRect(203, 398, 12, 12)   # ⌉ 右上
    assert corners[2].geometry() == QRect(5, 453, 12, 12)     # ⌊ 左下
    assert corners[3].geometry() == QRect(203, 453, 12, 12)   # ⌋ 右下


def test_sync_status_line_below_bubble(qapp):
    """状态行应位于气泡下方（台词与状态分离）。"""
    status = QLabel("")
    desktop_pet._sync_bubble_accessories(None, None, None, status, 30, 400, 200, 60)
    assert status.geometry() == QRect(38, 466, 184, 14)


def test_sync_does_not_touch_missing_side(qapp):
    """仅一侧存在（早期半构建状态）同样安全：不报错，已存在侧正常同步。"""
    footer = QLabel("wire ESTABLISHED")
    desktop_pet._sync_bubble_accessories(None, footer, None, None, 10, 400, 100, 40)
    assert footer.geometry() == QRect(10, 424, 100, 16)