"""桌面伴随窗口（桌宠）。

对应原 amadeus/src/app/companion/page.tsx。
特性：
- 透明背景（WA_TranslucentBackground）
- 无边框（FramelessWindowHint）
- 置顶（WindowStaysOnTopHint）
- 跳过任务栏（Tool）
- QWebEngineView 加载本地 HTML（pixi.js + Live2D Cubism 4）
- 鼠标拖动移动
- 右键菜单（隐藏 / 退出登录 / 退出程序）

Python ↔ JS 通信（后续步骤使用）：
- Python → JS：page.runJavaScript("setEmotion('smile')")
- JS → Python：QWebChannel 回传状态
"""
from __future__ import annotations

from typing import Optional
from pathlib import Path
import sys

from PySide6.QtCore import Qt, Signal, QPoint, QUrl, QEvent
from PySide6.QtGui import QMouseEvent, QAction
from PySide6.QtWidgets import QWidget, QMenu, QApplication, QPushButton
from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from config import Character, resources_path


# 窗口尺寸（桌宠通常较小）
WINDOW_W = 360
WINDOW_H = 540


class TransparentPage(QWebEnginePage):
    """透明背景的 WebEngine 页面。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setBackgroundColor(Qt.transparent)


class CompanionWindow(QWidget):
    """桌面伴随窗口（桌宠 Live2D）。"""

    logout_requested = Signal()  # 退出登录
    quit_requested = Signal()   # 退出整个程序

    def __init__(self, character: Character, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.character = character
        self.setWindowTitle(f"Amadeus · {character.name}")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.setFixedSize(WINDOW_W, WINDOW_H)

        # 拖动状态
        self._drag_pos: Optional[QPoint] = None

        # WebEngineView
        self._view = QWebEngineView(self)
        # 启用本地文件访问（file:// 加载模型需要）
        page_profile = self._view.page().profile()
        settings = self._view.page().settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.ShowScrollBars, False)

        # 替换为透明页
        transparent_page = TransparentPage(self._view)
        self._view.setPage(transparent_page)

        # 布局
        from PySide6.QtWidgets import QVBoxLayout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        # 加载 HTML
        html_path = Path(__file__).resolve().parent.parent / "live2d" / "live2d_page.html"
        model_abs = resources_path(character.live2d_path)
        model_url = QUrl.fromLocalFile(str(model_abs)).toString()
        html = html_path.read_text(encoding="utf-8").replace("__MODEL_URL__", model_url)
        base_url = QUrl.fromLocalFile(str(html_path.parent) + "/")
        self._view.setHtml(html, base_url)
        # 透明背景
        self._view.page().setBackgroundColor(Qt.transparent)

        self._handle = QPushButton("⋮⋮", self)
        self._handle.setToolTip("拖动桌宠 / 右键打开菜单")
        self._handle.setGeometry(WINDOW_W - 42, 8, 34, 28)
        self._handle.setStyleSheet(
            "QPushButton{background:rgba(10,10,16,90);color:rgba(255,255,255,150);border:0;border-radius:5px}"
            "QPushButton:hover{background:rgba(209,139,36,180);color:white}"
        )
        self._handle.installEventFilter(self)
        self._handle.raise_()

        # 初始位置（屏幕右下角）
        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - WINDOW_W - 80, screen.height() - WINDOW_H - 100)

    # ========== 鼠标拖动 ==========
    # 注：QWebEngineView 占满整个窗口，会"吃掉"鼠标事件
    # 这里通过事件过滤器在窗口层级截获鼠标按下 + 移动实现拖动
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ========== 右键菜单 ==========
    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        act_hide = menu.addAction("隐藏桌宠")
        act_logout = menu.addAction("退出登录")
        act_quit = menu.addAction("退出程序")
        menu.addSeparator()
        act_about = menu.addAction(f"角色：{self.character.name}")

        action = menu.exec(event.globalPos())
        if action is act_hide:
            self.hide()
        elif action is act_logout:
            self.logout_requested.emit()
        elif action is act_quit:
            self.quit_requested.emit()

    # ========== JS 通信桥（后续步骤使用） ==========
    def run_javascript(self, code: str) -> None:
        """在 Live2D 页面里执行 JS 代码（设置情绪、嘴型等）。"""
        self._view.page().runJavaScript(code)

    def set_emotion(self, emotion: str) -> None:
        """设置 Live2D 角色情绪。"""
        self.run_javascript(f"window.__amadeus && window.__amadeus.setEmotion && window.__amadeus.setEmotion({emotion!r});")

    def set_speaking(self, speaking: bool) -> None:
        value = "true" if speaking else "false"
        self.run_javascript(f"window.__amadeus && window.__amadeus.setSpeaking({value});")

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._handle:
            if event.type() == QEvent.MouseButtonPress:
                if event.button() == Qt.LeftButton:
                    self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    return True
                if event.button() == Qt.RightButton:
                    self._show_menu(event.globalPosition().toPoint())
                    return True
            if event.type() == QEvent.MouseMove and self._drag_pos is not None:
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                return True
            if event.type() == QEvent.MouseButtonRelease:
                self._drag_pos = None
                return True
        return super().eventFilter(watched, event)

    def _show_menu(self, global_pos) -> None:
        menu = QMenu(self)
        hide_action = menu.addAction("隐藏桌宠")
        logout_action = menu.addAction("退出登录")
        quit_action = menu.addAction("退出程序")
        action = menu.exec(global_pos)
        if action is hide_action:
            self.hide()
        elif action is logout_action:
            self.logout_requested.emit()
        elif action is quit_action:
            self.quit_requested.emit()
