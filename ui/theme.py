"""fauux 视觉主题资源（抖动纹理）——从 desktop_pet.py 提出。

本模块顶层不得 import PySide6；Qt 依赖函数内延迟导入。
"""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


# ============================================================
# fauux CRT 设计令牌（全局唯一来源）
# 所有界面（Qt QSS / QPainter / 终端 HTML）的配色与字体
# 一律从本模块引用，禁止在各文件重复书写 hex 值。
# ============================================================

# ---- 调色板 ----
ROSE = "#d2738a"    # 玫瑰：强调色 / 边框 / 提示符
CREAM = "#c1b492"   # 奶油：正文文字
DIM = "#8a7f63"     # 暗金：次要文字 / 待机状态
BG = "#171114"      # 深底：窗口 / 气泡背景
PANEL = "#21171b"   # 面板：控件底色
DEEP = "#08031a"    # 更深层背景
OK = "#7fb069"      # 成功/在线（协调 CRT 色调的绿，替代 iOS 绿）
WARN = "#d8a53f"    # 降级/警告

# ---- 字体 ----
FONT_MONO = "Consolas, Microsoft YaHei"          # QSS font-family 写法
QSS_FONT = '"Consolas", "Microsoft YaHei"'       # QSS font: 简写写法
FONT_SERIF = "Times New Roman"                   # 衬线装饰（气泡角括号等）
FONT_TITLE = "Courier New"                       # lainos 标题字体（CrtTitleBar）
FONT_TICKER = "Poiret One"                       # 设置页跑马灯字体

# ---- 终端 ----
TERMINAL_PROMPT = "guest@wired:~$"


# ============================================================
# 函数：qcolor()
# 作用：按设计令牌构造 QColor（十六进制字符串 + 可选 alpha），
#       供 QPainter 绘制代码使用，避免散落的 QColor(r,g,b,a)。
# 参数：
#   token str   令牌 hex（如 theme.ROSE）
#   alpha int   透明度 0~255（默认 255）
# 返回值：QColor
# ============================================================
def qcolor(token: str, alpha: int = 255):
    """按设计令牌构造 QColor（Qt 延迟导入，见模块头部约定）。"""
    from PySide6.QtGui import QColor
    color = QColor(token)
    color.setAlpha(alpha)
    return color


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
        img.fill(QColor(BG))
        p = QPainter(img)
        try:
            rose = qcolor(ROSE, 56)
            cream = qcolor(CREAM, 26)
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
