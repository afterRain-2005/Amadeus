"""fauux 视觉主题资源（抖动纹理）——从 desktop_pet.py 提出。

本模块顶层不得 import PySide6；Qt 依赖函数内延迟导入。
"""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


# ============================================================
# 函数：_dither_texture_url()
# 作用：返回 fauux 抖动纹理图片的绝对路径（正斜杠格式，
#       因为 QSS 的 url() 引用要求正斜杠，Windows 反斜杠会出错）。
# 参数：无
# 返回值：str —— 纹理图片的绝对路径
# ============================================================
def _dither_texture_url() -> str:
    """fauux 抖动纹理的绝对路径（正斜杠，供 QSS url() 引用）。"""
    return str(ROOT / "resources" / "textures" / "dither_rose.png").replace("\\", "/")


# ============================================================
# 函数：_ensure_dither_texture()
# 作用：首次运行时用 Qt 画图 API 生成 16×16 抖动纹理图片
#       （已存在则跳过）。生成失败时静默忽略（界面退化为纯色）。
# 参数：无
# 返回值：无（None）
# ============================================================
def _ensure_dither_texture() -> None:
    """首次运行时生成 16×16 抖动纹理（已存在则跳过）。失败退化为纯色。"""
    target = ROOT / "resources" / "textures" / "dither_rose.png"
    if target.exists():
        return
    try:
        from PySide6.QtGui import QColor, QImage, QPainter
        target.parent.mkdir(parents=True, exist_ok=True)
        img = QImage(16, 16, QImage.Format_ARGB32)
        img.fill(QColor("#171114"))
        p = QPainter(img)
        try:
            rose = QColor(210, 115, 138, 56)
            cream = QColor(193, 180, 146, 26)
            dark = QColor(0, 0, 0, 128)
            for y in range(0, 16, 4):
                for x in range(0, 16, 4):
                    p.setPen(rose);  p.drawPoint(x, y)
                    p.setPen(cream); p.drawPoint(x + 2, y + 2)
                    p.setPen(dark);  p.drawPoint(x + 1, y + 3)
                p.setPen(QColor(0, 0, 0, 40))
                p.drawLine(0, y + 3, 15, y + 3)
        finally:
            p.end()
        img.save(str(target), "PNG")
    except OSError:
        pass
