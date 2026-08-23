"""独立 CRT 命令行 agent 窗口（从 desktop_pet.run_overlay 提出）。

与 SettingsDialog 同级的独立窗口；通过 submitted/interrupt_requested 信号
与主窗口通信。设计令牌取自 fauux：rose #d2738a / cream #c1b492。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.terminal_commands import registry as terminal_command_registry
from ui.terminal_html import (
    _TERMINAL_CREAM,
    _TERMINAL_DIM,
    _TERMINAL_PROMPT,
    _TERMINAL_ROSE,
    _build_terminal_line_html,
    _complete_terminal_input,
    _line_cache_key,
)
from ui.theme import _dither_texture_url
from ui.widgets.crt_title_bar import CrtTitleBar


ROOT = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent.parent


# ============================================================
# 类：AgentTerminal（QDialog）
# 作用：★独立 CRT 命令行 agent 窗口（fauux 风格，类 Codex CLI）。
#       终端日志区 + 命令行输入框 + blink 光标 + CRT 特效
#       （扫描线/暗角/噪点/闪烁）+ 输入历史/
#       Tab 补全/Ctrl+C 中断。与 SettingsDialog 同级的独立窗口。
# ============================================================
class AgentTerminal(QDialog):
    """独立 CRT 命令行 agent 窗口（fauux 风格，类 Codex CLI）。

    与 SettingsDialog 同级的独立窗口。设计令牌取自
    fauux.neocities.org/stylesheet.css 实测：rose #d2738a 强调 /
    cream #c1b492 正文 / Times 衬线 / ⌈⌉ 角括号 / ║▒░ 分隔符 /
    blink 光标 / wiredB text-shadow rose 1px 4px 5px 辉光。
    """
    submitted = Signal(str)
    interrupt_requested = Signal()

    # ============================================================
    # 函数：__init__()
    # 作用：★构建整个终端窗口：标题栏（glitch + 辉光 + 关闭钮）、
    #       日志区（QTextBrowser）、输入行（提示符+输入框+blink光标）、
    #       各种定时器（blink/渲染节流/CRT闪烁/噪点）。
    #       关键细节：关闭按钮必须关掉 autoDefault（否则回车误触发
    #       关闭）；渲染用 33ms 节流合并 setHtml 防卡死。
    # 参数：
    #   parent QWidget|None 父控件
    # 返回值：无（None）
    # ============================================================
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Amadeus Terminal")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setObjectName("agentTerminal")
        self.setStyleSheet(
            "QDialog#agentTerminal{background-color:#171114;"
            "color:#c1b492;"
            "background-image:url(" + _dither_texture_url() + ");"
            "border:1px solid #d2738a;}"
        )
        self.setMinimumSize(560, 390)
        self.resize(720, 520)
        self._lines: list = []
        self._line_cache: dict = {}  # 行级 HTML 缓存，避免每 delta 全量 markdown 重渲染
        self._rendered_count: int = 0  # 已渲染进 QTextBrowser 的行数（增量刷新基准）
        self._needs_rebuild: bool = True  # 行列表被整体更换（任务切换）时强制全量重建
        self._line_starts: list[int] = []  # 每个逻辑行在 QTextDocument 中的起始位置
        self._dirty_from: int | None = None
        self._history: list[str] = []
        self._history_index: int = -1
        self._pending_approval: dict | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        self.title_bar = CrtTitleBar(
            "⌈ Ａｍａｄｅｕｓ Ｔｅｒｍｉｎａｌ ⌋",
            "wire ESTABLISHED · ch 1",
            self,
            self.close,
        )
        self.title = self.title_bar.title_label
        self.close_btn = self.title_bar.close_button
        layout.addWidget(self.title_bar)

        # 日志区（主体）
        self.log = QTextBrowser(self)
        self.log.setStyleSheet(
            "QTextBrowser{background-color:transparent;"
            f"color:{_TERMINAL_CREAM};border:1px solid {_TERMINAL_ROSE};"
            "border-radius:0px;padding:12px;font:14px 'Consolas','Microsoft YaHei'}"
            f"QTextBrowser{{selection-background-color:{_TERMINAL_ROSE};selection-color:#171114}}"
            f"QScrollBar:vertical{{background:rgba(210,115,138,0.15);width:6px;margin:4px}}"
            f"QScrollBar::handle:vertical{{background:{_TERMINAL_ROSE};border-radius:0px;min-height:30px}}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent}"
        )
        self.log.setOpenExternalLinks(False)
        # 超链接：默认不自动导航，Ctrl+点击用系统浏览器打开
        self.log.setOpenLinks(False)
        self.log.anchorClicked.connect(self._open_external_link)
        layout.addWidget(self.log, 1)

        # 审批条：危险工具请求直接在终端内确认，不打断 CRT 工作流
        self.approval_panel = QWidget(self)
        self.approval_panel.setStyleSheet(
            "QWidget{background:rgba(23,17,20,220);border:1px solid #d2738a;}"
            "QLabel{color:#c1b492;border:0;background:transparent;}"
            "QPushButton{color:#d2738a;background:#171114;border:1px solid #d2738a;"
            "padding:4px 8px;font:12px 'Consolas','Microsoft YaHei';}"
            "QPushButton:hover{color:#171114;background:#d2738a;}"
        )
        approval_layout = QVBoxLayout(self.approval_panel)
        approval_layout.setContentsMargins(8, 6, 8, 6)
        approval_layout.setSpacing(6)
        self.approval_label = QLabel(self.approval_panel)
        self.approval_label.setWordWrap(True)
        approval_layout.addWidget(self.approval_label)
        approval_buttons = QHBoxLayout()
        for label, choice in (
            ("仅本次", "once"),
            ("本次会话", "session"),
            ("始终允许", "always"),
            ("拒绝", "deny"),
        ):
            button = QPushButton(label, self.approval_panel)
            button.setAutoDefault(False)
            button.setDefault(False)
            button.clicked.connect(lambda _checked=False, value=choice: self._resolve_approval(value))
            approval_buttons.addWidget(button)
        approval_layout.addLayout(approval_buttons)
        self.approval_panel.hide()
        layout.addWidget(self.approval_panel)

        # 输入行：rose 提示符 + 输入框 + 闪烁块光标（下边框式）
        input_row = QHBoxLayout()
        input_row.setSpacing(6)
        prompt = QLabel(_TERMINAL_PROMPT, self)
        prompt.setStyleSheet(
            f"color:{_TERMINAL_ROSE};background:transparent;font:13px 'Consolas','Microsoft YaHei'"
        )
        self.input = QLineEdit(self)
        self.input.setStyleSheet(
            "QLineEdit{background:transparent;color:#c1b492;border:0;border-bottom:1px solid #d2738a;"
            "padding:4px 2px;font:13px 'Consolas','Microsoft YaHei'}"
            "QLineEdit::placeholder{color:#8a7f63}"
        )
        self.input.setPlaceholderText("say something to kurisu…")
        self.input.returnPressed.connect(self._submit)
        self.input.installEventFilter(self)
        # / 命令下拉补全面板：输入 / 时弹出，上下键选中，回车/Tab 填入命令
        self._slash_commands: list[tuple[str, str]] = terminal_command_registry.slash_completions()
        self._slash_panel = QListWidget(self)
        self._slash_panel.setStyleSheet(
            "QListWidget{background:#171114;color:#c1b492;border:1px solid #d2738a;"
            "font:12px 'Consolas','Microsoft YaHei';outline:0;}"
            "QListWidget::item{padding:5px 8px;}"
            "QListWidget::item:selected{background:#d2738a;color:#171114;}"
        )
        self._slash_panel.setFocusPolicy(Qt.NoFocus)
        self._slash_panel.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._slash_panel.hide()
        self._slash_panel.itemClicked.connect(self._slash_item_clicked)
        self.input.textChanged.connect(self._refresh_slash_panel)
        self.cursor = QLabel("█", self)
        self.cursor.setStyleSheet(
            f"color:{_TERMINAL_CREAM};background:transparent;font:13px 'Consolas','Microsoft YaHei'"
        )
        input_row.addWidget(prompt)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.cursor)
        layout.addLayout(input_row)

        # blink 光标：530ms 切换可见性（近似 CSS blink 50%→黑=隐没于黑底）
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(530)
        self._blink_timer.timeout.connect(lambda: self.cursor.setVisible(not self.cursor.isVisible()))
        self._blink_timer.start()

        # 渲染节流：流式 delta 高频到达时合并 setHtml，避免主线程被全量重渲染卡死
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(33)
        self._render_timer.timeout.connect(self._flush_render)

        # CRT 屏幕闪烁：整个窗口 opacity 轻微波动（参考 fauux 的 CRT 刷新感）
        self._flicker_effect = QGraphicsOpacityEffect(self)
        self._flicker_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._flicker_effect)
        self._flicker_timer = QTimer(self)
        self._flicker_timer.setInterval(70)
        self._flicker_timer.timeout.connect(self._flicker)
        self._flicker_timer.start()

        # CRT 背景特效：扫描线 + 暗角 + 静电噪点画在背景层，
        # 文字控件（log / 输入框）绘制其上，保持清晰不被扫描线/噪点覆盖
        self._noise_seed = 0
        self._noise_timer = QTimer(self)
        self._noise_timer.setInterval(80)
        self._noise_timer.timeout.connect(self._advance_noise)
        self._noise_timer.start()

        # 背景 logo 水印：加载后暗化，作为 CRT 背景的底层衬底
        # 背景图片（terminal_back.png）+ logo 水印：图片铺满终端，logo 叠放其上
        self._bg = self._load_background()
        self._bg_scaled = None  # 已按当前窗口尺寸缩放好的背景缓存
        self._bg_size = (0, 0)
        self._logo = self._load_logo()

    # ============================================================
    # 函数：resizeEvent()
    # 作用：终端尺寸变化时通知主窗口重新定位
    # 参数：
    #   event QResizeEvent 尺寸变化事件
    # 返回值：无（None）
    # ============================================================
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 终端固定在主窗口左侧：尺寸变化时通知主窗口重新定位
        p = self.parentWidget()
        if p is not None and hasattr(p, "_position_terminal"):
            p._position_terminal()

    # ============================================================
    # 函数：_flicker()
    # 作用：CRT 屏幕闪烁：窗口整体 opacity 在 0.98~1.0 之间随机微调，
    #       模拟老式 CRT 刷新感（70ms 定时器驱动）
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    def _flicker(self) -> None:
        import random
        # 0.98~1.0 之间微调 opacity，模拟老式 CRT 屏幕的轻微闪烁
        self._flicker_effect.setOpacity(0.98 + 0.02 * random.random())

    # ============================================================
    # 函数：_advance_noise()
    # 作用：静电噪点动画推进：更新随机种子并重绘（80ms 定时器驱动）
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    def _advance_noise(self) -> None:
        self._noise_seed = (self._noise_seed + 1) % 10000
        self.update()

    # ============================================================
    # 函数：paintEvent()
    # 作用：绘制终端背景（背景图片 → logo 水印 → 暗角 → 扫描线 → 噪点）
    # 参数：
    #   event QPaintEvent 绘制事件
    # 返回值：无（None）
    # ============================================================
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            self._paint_background(painter)
            self._paint_logo(painter)
            self._paint_vignette(painter)
            self._paint_scanlines(painter)
            self._paint_noise(painter)
        finally:
            painter.end()

    # ============================================================
    # 函数：_load_background()
    # 作用：加载终端背景图片 resources/terminal_back.png（原图，
    #       不处理），作为终端背景底层。文件缺失/加载失败返回 None
    # 参数：无
    # 返回值：QPixmap | None —— 背景图片
    # ============================================================
    def _load_background(self):
        """加载终端背景图片 resources/terminal_back.png（失败返回 None）。"""
        path = ROOT / "resources" / "terminal_back.png"
        if not path.exists():
            return None
        pix = QPixmap(str(path))
        return None if pix.isNull() else pix

    # ============================================================
    # 函数：_paint_background()
    # 作用：把背景图片按 cover 模式铺满整个终端窗口（保持比例，
    #       裁掉多余部分），并按窗口尺寸缓存缩放结果避免重复缩放。
    #       绘制后叠加暗化蒙版（半透明黑），保证终端文字可读
    # 参数：
    #   painter QPainter 绘制器
    # 返回值：无（None）
    # ============================================================
    def _paint_background(self, painter: QPainter) -> None:
        """把 terminal_back.png 按 cover 模式铺满终端背景，再叠加暗化蒙版。"""
        if self._bg is None:
            return
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        if self._bg_scaled is None or self._bg_size != (w, h):
            self._bg_scaled = self._bg.scaled(
                w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            self._bg_size = (w, h)
        x = (w - self._bg_scaled.width()) // 2
        y = (h - self._bg_scaled.height()) // 2
        painter.drawPixmap(x, y, self._bg_scaled)
        # 暗化蒙版：半透明黑整幅压暗，保证终端文字可读
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

    # ============================================================
    # 函数：_load_logo()
    # 作用：加载 amadeus 品牌 logo（amadeus-logo-TM.png）并暗化
    #       （透明度降至 0.55，保留 alpha），作为终端背景水印叠放
    #       在背景图片之上。文件缺失/加载失败返回 None
    # 参数：无
    # 返回值：QPixmap | None —— 暗化后的 logo
    # ============================================================
    def _load_logo(self):
        """加载 amadeus logo 并暗化（0.55 透明度），作为终端背景水印。"""
        path = ROOT / "amadeus-logo-TM.png"
        if not path.exists():
            return None
        src = QPixmap(str(path))
        if src.isNull():
            return None
        # 暗化：在透明底上以 0.55 透明度重绘，得到低对比水印（保留 alpha）
        dark = QPixmap(src.size())
        dark.fill(Qt.GlobalColor.transparent)
        p = QPainter(dark)
        try:
            p.setOpacity(0.55)
            p.drawPixmap(0, 0, src)
        finally:
            p.end()
        return dark

    # ============================================================
    # 函数：_paint_logo()
    # 作用：把 logo 原图绘制为终端底部背景中央的水印（输入行上方），
    #       叠放在背景图片之上
    # 参数：
    #   painter QPainter 绘制器
    # 返回值：无（None）
    # ============================================================
    def _paint_logo(self, painter: QPainter) -> None:
        """把 logo 原图绘制为终端底部背景中央的水印（输入行之上）。"""
        if self._logo is None:
            return
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        pw = int(w * 0.42)
        ph = int(self._logo.height() * pw / self._logo.width())
        if ph > int(h * 0.30):
            ph = int(h * 0.30)
            pw = int(self._logo.width() * ph / self._logo.height())
        scaled = self._logo.scaled(pw, ph, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (w - scaled.width()) // 2
        # 底部背景中央：垂直中线位于窗口 84% 高度处（输入行上方留白）
        y = int(h * 0.84) - scaled.height() // 2
        painter.save()
        painter.setOpacity(0.8)
        painter.drawPixmap(x, y, scaled)
        painter.restore()

    def _paint_vignette(self, painter: QPainter) -> None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        gradient = QRadialGradient(w / 2, h / 2, max(w, h) * 0.75)
        gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        gradient.setColorAt(0.55, QColor(0, 0, 0, 0))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 175))
        painter.fillRect(self.rect(), gradient)

    def _paint_scanlines(self, painter: QPainter) -> None:
        painter.setPen(QColor(0, 0, 0, 70))
        for y in range(0, self.height(), 3):
            painter.drawLine(0, y, self.width(), y)

    def _paint_noise(self, painter: QPainter) -> None:
        import random
        rnd = random.Random(self._noise_seed)
        painter.setPen(QColor(193, 180, 146, 45))
        count = max(0, self.width() * self.height() // 100)
        for _ in range(count):
            painter.drawPoint(rnd.randrange(self.width()), rnd.randrange(self.height()))

    # ============================================================
    # 函数：_paint_vignette()
    # 作用：绘制暗角（radial 渐变，边缘渐黑 175 透明度），
    #       模拟 CRT 屏幕四角变暗的效果
    # 参数：
    #   painter QPainter 绘制器
    # 返回值：无（None）
    # ============================================================
    def _paint_vignette(self, painter: QPainter) -> None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        gradient = QRadialGradient(w / 2, h / 2, max(w, h) * 0.75)
        gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        gradient.setColorAt(0.55, QColor(0, 0, 0, 0))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 175))
        painter.fillRect(self.rect(), gradient)

    # ============================================================
    # 函数：_paint_scanlines()
    # 作用：绘制水平扫描线（每 3px 一条半透明黑线，CRT 特征效果）
    # 参数：
    #   painter QPainter 绘制器
    # 返回值：无（None）
    # ============================================================
    def _paint_scanlines(self, painter: QPainter) -> None:
        painter.setPen(QColor(0, 0, 0, 70))
        for y in range(0, self.height(), 3):
            painter.drawLine(0, y, self.width(), y)

    # ============================================================
    # 函数：_paint_noise()
    # 作用：绘制静电噪点（随机分布的半透明点，数量约为面积/100）
    # 参数：
    #   painter QPainter 绘制器
    # 返回值：无（None）
    # ============================================================
    def _paint_noise(self, painter: QPainter) -> None:
        import random
        rnd = random.Random(self._noise_seed)
        painter.setPen(QColor(193, 180, 146, 45))
        count = max(0, self.width() * self.height() // 100)
        for _ in range(count):
            painter.drawPoint(rnd.randrange(self.width()), rnd.randrange(self.height()))

    # ============================================================
    # 函数：keyPressEvent()
    # 作用：终端键盘快捷键：Ctrl+Plus/Minus=字体缩放、Ctrl+C=中断任务
    # 参数：
    #   event QKeyEvent 键盘事件
    # 返回值：无（None）
    # ============================================================
    def keyPressEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier:
            if event.key() in (Qt.Key_Plus, Qt.Key_Equal):
                self.log.zoomIn(1)
                event.accept()
                return
            if event.key() == Qt.Key_Minus:
                self.log.zoomOut(1)
                event.accept()
                return
            if event.key() == Qt.Key_C:
                self.interrupt_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)

    # ============================================================
    # 函数：eventFilter()
    # 作用：监听输入框键盘事件：Ctrl+C=中断、Up/Down=历史、Tab=补全
    # 参数：
    #   obj   QObject 事件来源对象
    #   event QEvent   事件
    # 返回值：bool —— True=事件已处理；False=继续传给父类
    # ============================================================
    def eventFilter(self, obj, event) -> bool:
        if obj is self.input and event.type() == QEvent.KeyPress:
            key = event.key()
            if event.modifiers() & Qt.ControlModifier and key == Qt.Key_C:
                self.interrupt_requested.emit()
                return True
            # / 命令面板可见时，方向键/Tab/回车优先导航面板，而不是翻历史/提交
            if self._slash_panel.isVisible():
                if key == Qt.Key_Escape:
                    self._hide_slash_panel()
                    return True
                if key == Qt.Key_Up:
                    self._slash_move(-1)
                    return True
                if key == Qt.Key_Down:
                    self._slash_move(1)
                    return True
                if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
                    self._slash_accept()
                    return True
            if key == Qt.Key_Up:
                self._history_prev()
                return True
            if key == Qt.Key_Down:
                self._history_next()
                return True
            if key == Qt.Key_Tab:
                self._tab_complete()
                return True
        return super().eventFilter(obj, event)

    # ============================================================
    # 函数：_history_prev()
    # 作用：上一条输入历史（Up 键）——倒序翻历史记录
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    def _history_prev(self) -> None:
        if not self._history:
            return
        if self._history_index < 0:
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        self.input.setText(self._history[self._history_index])
        self.input.setCursorPosition(len(self.input.text()))

    # ============================================================
    # 函数：_history_next()
    # 作用：下一条输入历史（Down 键）——正序翻历史，到底后清空
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    def _history_next(self) -> None:
        if self._history_index < 0:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self.input.setText(self._history[self._history_index])
        else:
            self._history_index = -1
            self.input.setText("")
        self.input.setCursorPosition(len(self.input.text()))

    # ============================================================
    # 函数：_tab_complete()
    # 作用：Tab 补全：先匹配历史命令，否则文件路径补全
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    def _tab_complete(self) -> None:
        parent = self.parentWidget()
        cwd = getattr(parent, "_terminal_cwd", None)
        completed = _complete_terminal_input(self.input.text(), self._history, cwd)
        if completed is None:
            return
        self.input.setText(completed)
        self.input.setCursorPosition(len(completed))

    # ============================================================
    # 函数：_refresh_slash_panel()
    # 作用：输入框文本变化时刷新 / 命令下拉面板：仅当输入以 / 开头
    #       且尚未输入参数（无空格）时，按已输入字符过滤命令并显示；
    #       否则隐藏面板。
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    def _refresh_slash_panel(self) -> None:
        text = self.input.text()
        if not text.startswith("/") or " " in text:
            self._hide_slash_panel()
            return
        token = text[1:].lower()
        matches = [
            (name, desc)
            for name, desc in self._slash_commands
            if name.lower().startswith(token)
        ]
        if not matches:
            self._hide_slash_panel()
            return
        self._slash_panel.clear()
        for name, desc in matches:
            item = QListWidgetItem(f"/{name}  —  {desc}")
            item.setData(Qt.UserRole, name)
            self._slash_panel.addItem(item)
        self._slash_panel.setCurrentRow(0)
        self._position_slash_panel()
        self._slash_panel.show()
        self._slash_panel.raise_()

    # ============================================================
    # 函数：_position_slash_panel()
    # 作用：把 / 命令面板定位到输入框正上方，宽度与输入框一致
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    def _position_slash_panel(self) -> None:
        top_left = self.input.mapTo(self, QPoint(0, 0))
        panel_w = self.input.width()
        # 每项约 26px，最多 8 项；至少给 1 项高度
        item_h = 26
        panel_h = min(self._slash_panel.count(), 8) * item_h + 4
        self._slash_panel.setGeometry(
            top_left.x(), top_left.y() - panel_h, panel_w, panel_h
        )

    def _hide_slash_panel(self) -> None:
        self._slash_panel.hide()

    # ============================================================
    # 函数：_slash_move()
    # 作用：面板可见时上下移动选中项（循环）
    # 参数：
    #   delta int +1 下移 / -1 上移
    # 返回值：无（None）
    # ============================================================
    def _slash_move(self, delta: int) -> None:
        count = self._slash_panel.count()
        if count == 0:
            return
        self._slash_panel.setCurrentRow((self._slash_panel.currentRow() + delta) % count)

    # ============================================================
    # 函数：_slash_accept()
    # 作用：确认当前选中命令：把 /命令名 填回输入框并聚焦光标到末尾
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    def _slash_accept(self) -> None:
        item = self._slash_panel.currentItem()
        if item is not None:
            name = item.data(Qt.UserRole)
            self.input.setText("/" + name + " ")
        self._hide_slash_panel()
        self.input.setFocus()
        self.input.setCursorPosition(len(self.input.text()))

    def _slash_item_clicked(self, item) -> None:
        """鼠标点击面板项：填入命令并隐藏面板。"""
        name = item.data(Qt.UserRole)
        self.input.setText("/" + name + " ")
        self._hide_slash_panel()
        self.input.setFocus()
        self.input.setCursorPosition(len(self.input.text()))

    # ============================================================
    # 函数：_interrupt()
    # 作用：Ctrl+C 中断当前正在运行的 harness 回合（仅 harness 模式生效）
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    # ============================================================
    # 函数：_submit()
    # 作用：提交输入：存入历史（最多 200 条）→ 发出 submitted 信号 →
    #       清空输入框。主窗口接收信号后发送给 AI。
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    def _submit(self) -> None:
        text = self.input.text().strip()
        if text:
            if not self._history or self._history[-1] != text:
                self._history.append(text)
                if len(self._history) > 200:
                    self._history.pop(0)
            self._history_index = -1
            self.submitted.emit(text)
            self.input.clear()

    def request_approval(self, request: dict) -> None:
        """在终端内显示一个待处理的工具审批请求。"""
        if self._pending_approval is not None:
            self._resolve_approval("deny")
        payload = request.get("payload") or {}
        command = str(payload.get("command", "") or "tool")
        description = str(payload.get("description", "") or command)
        self._pending_approval = request
        self.approval_label.setText(f"⚠ {command}\n{description}\n允许执行吗？")
        self.approval_panel.show()

    def _resolve_approval(self, choice: str) -> None:
        request = self._pending_approval
        if request is None:
            return
        self._pending_approval = None
        self.approval_panel.hide()
        request["choice"] = choice
        request["event"].set()

    def closeEvent(self, event) -> None:
        self._resolve_approval("deny")
        super().closeEvent(event)

    # ============================================================
    # 函数：render_lines()
    # 作用：标记待渲染的终端行并启动 33ms 节流定时器（实际刷新在
    #       _flush_render 合并执行）。full=True=整体更换（全量重建），
    #       默认增量追加
    # 参数：
    #   lines list  终端行列表，每项 (kind, text) 或 (kind, text, extra)
    #   full  bool  是否强制全量重建（默认 False）
    # 返回值：无（None）
    # ============================================================
    def render_lines(
        self,
        lines: list,
        full: bool = False,
        dirty_from: int | None = None,
    ) -> None:
        """标记待渲染并启动节流定时器；实际刷新在 _flush_render 中合并执行。

        full=True 表示行列表被整体更换（新任务/重开终端），需全量重建；
        默认增量：只追加新行并替换流式末行。
        """
        self._lines = lines
        if full:
            self._needs_rebuild = True
        if dirty_from is not None:
            dirty_from = max(0, dirty_from)
            self._dirty_from = (
                dirty_from
                if self._dirty_from is None
                else min(self._dirty_from, dirty_from)
            )
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _flush_render(self) -> None:
        """从首个脏逻辑行重绘后缀，保留前缀且不丢多块 HTML。"""
        lines = self._lines
        if self._needs_rebuild or len(lines) < self._rendered_count:
            self._rebuild_all(lines)
            return
        if not lines:
            self._dirty_from = None
            return
        if self._rendered_count == 0 or not self._line_starts:
            self._rebuild_all(lines)
            return
        if self._dirty_from is not None:
            start_index = min(self._dirty_from, self._rendered_count - 1)
        elif len(lines) > self._rendered_count:
            start_index = self._rendered_count - 1
        else:
            start_index = len(lines) - 1
        self._replace_suffix(start_index)
        self._rendered_count = len(lines)
        self._dirty_from = None
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _rebuild_all(self, lines: list) -> None:
        """全量重建并记录每个逻辑行的文档位置。"""
        from PySide6.QtGui import QTextCursor

        self._needs_rebuild = False
        self._rendered_count = len(lines)
        self._dirty_from = None
        self._line_starts = []
        header = "".join([
            f"<div style='color:{_TERMINAL_DIM};font-size:9px'>║▒░ amadeus shell — wired session</div>",
            f"<div style='border-top:1px solid {_TERMINAL_ROSE};margin:2px 0 6px 0'></div>",
        ])
        self.log.clear()
        cursor = QTextCursor(self.log.document())
        cursor.insertHtml(header)
        for index in range(len(lines)):
            if cursor.position() > 0:
                cursor.insertBlock()
            self._line_starts.append(cursor.position())
            cursor.insertHtml(self._line_html(index))
        self.log.setTextCursor(cursor)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _replace_suffix(self, start_index: int) -> None:
        """删除 start_index 起的文档后缀，再按当前逻辑行重插。"""
        from PySide6.QtGui import QTextCursor

        if start_index >= len(self._line_starts):
            self._rebuild_all(self._lines)
            return
        cursor = QTextCursor(self.log.document())
        cursor.setPosition(self._line_starts[start_index])
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        del self._line_starts[start_index:]
        for offset, index in enumerate(range(start_index, len(self._lines))):
            if offset > 0 and cursor.position() > 0:
                cursor.insertBlock()
            self._line_starts.append(cursor.position())
            cursor.insertHtml(self._line_html(index))
        self.log.setTextCursor(cursor)

    def _line_html(self, index: int) -> str:
        """构建第 index 行 HTML：末行不缓存（流式中间态），历史行按 key 缓存。"""
        item = self._lines[index]
        if len(item) == 3:
            kind, text, extra = item
        else:
            kind, text, extra = item[0], item[1], None
        if index == len(self._lines) - 1:
            return _build_terminal_line_html(kind, text, extra)
        key = _line_cache_key(item)
        line_html = self._line_cache.get(key)
        if line_html is None:
            line_html = _build_terminal_line_html(kind, text, extra)
            self._line_cache[key] = line_html
        return line_html

    def _scroll_to_bottom(self) -> None:
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _open_external_link(self, url) -> None:
        """Ctrl+点击超链接：用系统默认浏览器打开（普通点击不响应）。"""
        if not (QApplication.keyboardModifiers() & Qt.ControlModifier):
            return
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(url)

    def set_busy(self, busy: bool) -> None:
        # 保持输入框可接收 Ctrl+C；禁用控件会让焦点事件无法到达中断处理。
        self.input.setReadOnly(busy)
