"""Pure-Python Live2D desktop pet with a native transparent overlay."""
from __future__ import annotations

import html
import multiprocessing as mp
from multiprocessing.connection import Connection
from pathlib import Path
import os
import sys
import threading
import time
import traceback
import uuid


ROOT = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
# 本地依赖目录：用 pip install --target 装的 ddgs/trafilatura 等（避开 anaconda site-packages 沙箱限制）。
# 冻结模式下依赖已打包进 exe，不需要 .libs。
if not getattr(sys, 'frozen', False):
    _libs = ROOT / ".libs"
    if _libs.is_dir():
        sys.path.insert(0, str(_libs))
# 通信走 mp.Pipe(duplex=True) 双向管道（frame 下行 / command 上行），
# 不再用 data/pet_command.json 文件轮询。
from core.single_instance import acquire_single_instance
from core.storage import APP_DIR as _APP_DIR

# 气泡纯函数已抽出至 ui/bubble.py（重构 2026-08-24）。
# 此处再导出保持 desktop_pet.* 旧命名空间兼容（tests/ 与历史引用）。
from core.diag import _write_runtime_log  # noqa: F401
from core.voice.gpt_sovits_proc import (  # noqa: F401
    _locate_gpt_sovits,
    maybe_start_gpt_sovits,
    _warmup_gpt_sovits,
    _start_ssh_tunnel,
    stop_gpt_sovits,
    _gpt_sovits_proc,
    _ssh_tunnel,
)

from ui.renderer_proc import QuietHandler, free_port, renderer_process  # noqa: F401

from ui.bubble import (  # noqa: F401
    _sync_bubble_accessories,
    _wrap_bubble_html,
    _bubble_size_hint,
    _streamed_display_text,
    _merge_bubble_segments,
    _final_bubble_segments,
    _split_stream_segments,
    _decide_delta_action,
    _decide_send_instant_action,
    _decide_call_toggle_action,
)

# 抖动纹理助手已抽出至 ui/theme.py（重构 2026-08-24）。
from ui.theme import _dither_texture_url, _ensure_dither_texture  # noqa: F401

# 终端 HTML 构建纯函数已抽出至 ui/terminal_html.py（重构 2026-08-24）。
from ui.terminal_html import (  # noqa: F401
    _TERMINAL_ROSE,
    _TERMINAL_CREAM,
    _TERMINAL_DIM,
    _TERMINAL_PROMPT,
    _render_markdown,
    _build_terminal_line_html,
    _render_diff_html,
    _editor_diff_extra,
    _tool_args_summary,
    _terminal_token_start,
    _complete_terminal_input,
    _line_cache_key,
    _build_terminal_html,
)
READY_FILE = _APP_DIR / "desktop_pet.ready"


# ============================================================
# 函数：run_overlay()
# 作用：★桌宠 UI 主进程的完整实现（本文件最大的函数，~2000 行）。
#       在这里创建 Qt 应用程序和所有 UI 组件：
#       - PetWindow（主窗口：Live2D 覆盖层 + 气泡/Dock/终端/设置）
#       - 各种内部类：AgentTask（AI 请求后台任务）、DockButton、Toolbar、
#         ReplyBubble、TerminalView 等
#       流程：创建 QApplication → 构造 PetWindow → 进入 Qt 事件循环
#       （app.exec()），直到退出返回退出码。
#       ★PySide6 相关导入全部放在本函数内部（延迟导入）——因为
#       desktop_pet.py 主进程顶层绝对不能 import PySide6，否则 renderer
#       子进程的 QtWebEngine 渲染会崩（lessons 8-15 重大事故）。
# 参数：
#   connection Connection mp.Pipe 的一端（主进程端），与 renderer 子进程通信
#   renderer   mp.Process renderer 子进程对象（退出时需 terminate）
# 返回值：int —— Qt 事件循环退出码（0=正常）
# ============================================================
def run_overlay(connection: Connection, renderer: mp.Process) -> int:
    from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRect, Qt, QThreadPool, QTimer
    from PySide6.QtGui import QColor, QCursor, QIcon, QImage, QKeyEvent, QMouseEvent, QPainter, QPixmap
    from PySide6.QtWidgets import (
                QApplication, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                QPushButton, QSystemTrayIcon, QWidget,
                QGraphicsOpacityEffect,
            )

    from config import PHONE_DEFAULTS, get_character_by_id, get_random_greeting
    from core.emotion_parser import parse_reply
    from core.ipc_command import serialize_command
    from core.session_manager import active_session, add_message, create_session, load_state, save_state
    from core.skills import SkillManager, build_skill_prompt
    from core.terminal_commands import TerminalCommandContext, registry as terminal_command_registry
    from core.storage import load_config
    from core.terminal_state import load_terminal_state, save_terminal_state
    from core.voice.tts_client import SpeechPlayer
    from ui.settings_dialog import SettingsDialog
    from ui.widgets.crt_overlay import CrtOverlay
    from ui.widgets.agent_task import AgentTask
    from ui.widgets.agent_terminal import AgentTerminal
    from ui.widgets.dock import DockBar
    from ui.widgets.status_bar import StatusBar

    character = get_character_by_id("kurisu")

    # ============================================================
    # 函数：send_command()
    # 作用：overlay 主进程 → renderer 子进程发送命令（如 emotion 表情、
    #       speaking 口型），通过 duplex 管道发送序列化后的命令。
    #       管道断裂（renderer 已退出）时静默忽略，不崩溃。
    # 参数：
    #   **payload dict —— 命令参数（如 {"type": "emotion", "value": "blush"}）
    # 返回值：无（None）
    # ============================================================
    def send_command(**payload) -> None:
        """overlay→renderer 发送命令（emotion/speaking），走 duplex 管道。"""
        try:
            connection.send(serialize_command(**payload))
        except (BrokenPipeError, OSError):
            pass

    class PetWindow(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self._frame = QImage()
            self._first_frame_received = False
            self._drag_offset: QPoint | None = None
            self._busy = False
            self._streamed_reply = ""
            self._hotkey_down = False
            self._csa_down = False
            self.terminal = None  # AgentTerminal 独立窗口，懒创建
            self._term_lines: list = []
            self._terminal_reply_index: int | None = None
            self._terminal_route_mode = "auto"
            self._terminal_cwd = ROOT
            self._active_agent_task = None
            terminal_state = load_terminal_state()
            self._terminal_session_id = terminal_state["session_id"]
            persisted_cwd = terminal_state.get("cwd")
            if persisted_cwd:
                candidate_cwd = Path(persisted_cwd).expanduser()
                if candidate_cwd.is_dir():
                    self._terminal_cwd = candidate_cwd.resolve()
            if terminal_state.get("route") in {"auto", "local", "harness"}:
                self._terminal_route_mode = terminal_state["route"]
            self._skill_manager = SkillManager()
            self._active_skills: dict[str, object] = {}
            self._zoom = 0.9
            self._pinned = False
            self._bubble_segments: list[str] = []
            self._bubble_index = 0
            # 流式期间用户是否已单击进入逐句阅读（进入后流式文字不再覆盖当前句）；
            # _stream_live = 已追平所有完成句（此后残句尾巴继续打字机直播）
            self._stream_reading = False
            self._stream_live = False
            self._bubble_timer: QTimer | None = None
            self._snap_anim: QPropertyAnimation | None = None
            self._user_pos: QPoint | None = None
            self._was_desktop = False
            self._inactivity_timer = QTimer(self)
            self._inactivity_timer.setSingleShot(True)
            self._inactivity_timer.timeout.connect(self._hide_idle_bubble)
            self._state = load_state(character.id, get_random_greeting(character.id))
            self.speech = SpeechPlayer(self)
            self.speech.speaking_changed.connect(lambda value: send_command(speaking=value))
            # 音量强度 → Live2D 口型开合（mouth_intensity 在播放线程发射，queued 回主线程）
            self.speech.mouth_intensity.connect(lambda value: send_command(mouth=value))
            # 语音服务离线：气泡序列末尾追加提示（信号在 TTS 工作线程发射，
            # queued connection 回到主线程，追加列表安全）
            self.speech.tts_offline.connect(self._notify_tts_offline)
            self.setWindowTitle("牧濑红莉栖 [PY]")
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_NoSystemBackground, True)
            # 尺寸对齐 phone_live2d_page.html 的合成画面：304×690
            # （手机本体 280×560 —— 严格 2:1 比例）
            self.setFixedSize(304, 690)

            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.right() - self.width() - 20, screen.bottom() - self.height() - 60)

            self.reply_bubble = QLabel(self)
            # 气泡：手机屏幕内、Dock 栏上方（几何由 _set_bubble_text 统一计算）
            self.reply_bubble.setGeometry(28, 448, 248, 96)
            # v4：正文左对齐（富文本 line-height 1.5 由 _wrap_bubble_html 提供）
            self.reply_bubble.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.reply_bubble.setWordWrap(True)
            self.reply_bubble.setStyleSheet(
                "QLabel{background-color:#171114;background-image:url(" + _dither_texture_url() + ");"
                "color:#c1b492;"
                "border-left:1px solid #d2738a;border-right:1px solid #d2738a;"
                "border-top:1.5px solid #d2738a;border-bottom:1.5px solid #d2738a;"
                "border-radius:0px;"
                "padding:10px 16px;font:14px 'Consolas','Microsoft YaHei';"
                "font-weight:400}"
            )
            _init_msgs = active_session(self._state)["messages"]
            if _init_msgs:
                self._set_bubble_text(self._latest_line(_init_msgs[-1]["content"]))
            self.reply_bubble.hide()
            self._bubble_crt = CrtOverlay(self.reply_bubble, scanlines=True, vignette=False, noise=False)
            self._bubble_crt.raise_()

            # fauux 稿⑤⑩④：气泡头部名牌 + 底部状态注脚（随气泡 Show/Hide 联动）
            # v4：名牌/注脚改为一体标签（bg + 粗左边条，贴合气泡上下缘）
            self.bubble_header = QLabel("K U R I S U", self)
            self.bubble_header.setStyleSheet(
                "color:#d2738a;background-color:#171114;"
                "border:1px solid #d2738a;border-left:6px solid #d2738a;border-radius:0px;"
                "font-family:'Times New Roman','Times',serif;font-size:11px;font-weight:bold;"
            )
            self.bubble_header.setAlignment(Qt.AlignHCenter)
            self.bubble_header.hide()
            self.bubble_footer = QLabel("wire ESTABLISHED · Δ 0.41s · ch 1", self)
            self.bubble_footer.setStyleSheet(
                "color:#8a7f63;background-color:#171114;"
                "border:1px solid #8a7f63;border-left:6px solid #8a7f63;border-radius:0px;"
                "font-family:'Consolas','Microsoft YaHei';font-size:9px;"
            )
            self.bubble_footer.setAlignment(Qt.AlignHCenter)
            self.bubble_footer.hide()
            # v4：气泡四角括号（fauux ⌈⌉⌊⌋，随气泡显示/隐藏联动）
            self.bubble_corners = []
            for _ch in ("⌈", "⌉", "⌊", "⌋"):
                lbl = QLabel(_ch, self)
                lbl.setStyleSheet(
                    "color:#d2738a;background:transparent;"
                    "font-family:'Times New Roman','Times',serif;font-size:15px;font-weight:bold;"
                )
                lbl.hide()
                self.bubble_corners.append(lbl)
            # v4：状态行（工具进度与台词分离，dim 小字）
            self.status_line = QLabel("", self)
            self.status_line.setStyleSheet(
                "color:#8a7f63;background:transparent;"
                "font-family:'Consolas','Microsoft YaHei';font-size:9px;"
            )
            self.status_line.setAlignment(Qt.AlignHCenter)
            self.status_line.hide()
            self.reply_bubble.installEventFilter(self)

            # 输入面板（默认隐藏，点击💬展开；屏幕内底部槽位）
            panel_w = 248
            panel_x = 20 + (264 - panel_w) // 2   # 20 = 屏幕左，264 = 屏幕宽
            panel_y = 118 + 496 - 64 - 2            # 屏幕底 - 64 Dock预留 - 2margin
            self.input_panel = QWidget(self)
            self.input_panel.setGeometry(panel_x, panel_y, panel_w, 52)
            self.input_panel.setStyleSheet(
                "background-color:#171114;background-image:url(" + _dither_texture_url() + ");"
                "border-left:1px solid #d2738a;border-right:1px solid #d2738a;"
                "border-top:1.5px solid #d2738a;border-bottom:1.5px solid #d2738a;"
                "border-radius:0px"
            )
            input_layout = QHBoxLayout(self.input_panel)
            input_layout.setContentsMargins(14, 6, 6, 6)
            input_layout.setSpacing(4)
            self.input = QLineEdit()
            self.input.setPlaceholderText("和红莉栖对话…")
            self.input.setStyleSheet(
                "QLineEdit{background:transparent;color:#c1b492;border:0;padding:8px 10px;"
                "font-size:14px;font-family:'Consolas','Microsoft YaHei'}"
                "QLineEdit::placeholder{color:#8a7f63}"
            )
            self.input.returnPressed.connect(self._send)
            collapse_button = QPushButton("←")
            collapse_button.setToolTip("收起聊天")
            collapse_button.clicked.connect(self._toggle_input_panel)
            collapse_button.setFixedSize(36, 36)
            collapse_button.setStyleSheet(
                "QPushButton{background:transparent;color:#8a7f63;border:1px solid #8a7f63;border-radius:0px;"
                "font-size:16px}"
                "QPushButton:hover{background:#d2738a;color:#171114}"
            )
            input_layout.addWidget(collapse_button)
            input_layout.addWidget(self.input, 1)
            send_button = QPushButton("↑")
            send_button.setToolTip("发送")
            send_button.clicked.connect(self._send)
            send_button.setFixedSize(36, 36)
            send_button.setStyleSheet(
                "QPushButton{background:transparent;color:#c1b492;border:1px solid #c1b492;border-radius:0px;"
                "font-size:16px;font-weight:bold}"
                "QPushButton:hover{background:#d2738a;color:#171114} QPushButton:disabled{color:#8a7f63}"
            )
            input_layout.addWidget(send_button)
            self.send_button = send_button
            self.input_panel.hide()
            self._panel_crt = CrtOverlay(self.input_panel, scanlines=True, vignette=False, noise=False)
            self._panel_crt.raise_()

            # Dock 底部悬浮工具栏
            self.dock_bar = DockBar(self)
            self.dock_bar.button("对话").clicked.connect(self._toggle_input_panel)
            self.dock_bar.button("固定").clicked.connect(self._toggle_pin)
            self.dock_bar.button("设置").clicked.connect(lambda: SettingsDialog(self).exec())
            self.dock_bar.button("终端").clicked.connect(self._toggle_terminal)
            self.dock_bar.button("退出").clicked.connect(QApplication.quit)
            self.dock_bar.button("电话").clicked.connect(self._toggle_call)
            self.dock_bar.show()

            # 手机屏幕顶部状态栏（时间 + 信号，Qt 实现）
            self.status_bar = StatusBar(self)
            self.status_bar.show()
            self.status_bar.raise_()

            # 通话态视图（默认隐藏，覆盖屏幕区域）
            from ui.widgets.call_view import CallView
            self._in_call = False
            self.call_view = CallView(self)
            self.call_view.setGeometry(20, 118, 264, 496)
            self.call_view.hide()
            self.call_controller = None  # 通话时创建，避免闲置时持有 sounddevice stream

            # Dock 与输入框互斥切换的 opacity effect
            self._dock_opacity = QGraphicsOpacityEffect(self.dock_bar)
            self.dock_bar.setGraphicsEffect(self._dock_opacity)
            self._dock_opacity.setOpacity(1.0)

            self._input_opacity = QGraphicsOpacityEffect(self.input_panel)
            self.input_panel.setGraphicsEffect(self._input_opacity)
            self._input_opacity.setOpacity(0.0)
            # 收起动画挂起的 hide 定时器（防止淡出中途再次展开后被误隐藏）
            self._pending_panel_hide: QTimer | None = None

            self._relayout()

            self.tray = QSystemTrayIcon(self)
            # v4：tray 图标用项目 logo 缩略；缺失时回落红色方块（避免空图标）
            icon_pixmap = QPixmap(str(ROOT / "resources" / "amadeus-logo-TM.png"))
            if icon_pixmap.isNull():
                icon_pixmap = QPixmap(24, 24)
                icon_pixmap.fill(QColor(210, 115, 138))
            else:
                icon_pixmap = icon_pixmap.scaled(
                    24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            self.tray.setIcon(QIcon(icon_pixmap))
            tray_menu = QMenu()
            restore_action = tray_menu.addAction("显示红莉栖")
            focus_action = tray_menu.addAction("聚焦输入")
            quit_action = tray_menu.addAction("退出")
            restore_action.triggered.connect(self._restore_from_tray)
            focus_action.triggered.connect(self._focus_input)
            quit_action.triggered.connect(QApplication.quit)
            self.tray.setContextMenu(tray_menu)
            self.tray.activated.connect(self._tray_activated)
            self.tray.show()

            # 最小化时的悬浮恢复按钮（独立窗口）
            self._restore_win = QWidget()
            self._restore_win.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            self._restore_win.setAttribute(Qt.WA_TranslucentBackground, True)
            self._restore_win.setFixedSize(48, 48)
            screen = QApplication.primaryScreen().availableGeometry()
            self._restore_win.move(screen.right() - 58, screen.bottom() - 120)
            btn = QPushButton(self._restore_win)
            btn.setGeometry(0, 0, 48, 48)
            btn.setToolTip("点击打开红莉栖")
            btn.setCursor(Qt.PointingHandCursor)
            # 加载 amadeus logo 作为按钮图标（44×44，居中留 2px 边距）
            _logo_path = ROOT / "resources" / "amadeus-logo-TM.png"
            if _logo_path.exists():
                _logo_pix = QPixmap(str(_logo_path)).scaled(
                    44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                btn.setIcon(QIcon(_logo_pix))
                btn.setIconSize(_logo_pix.size())
            btn.setStyleSheet(
                "QPushButton{background:transparent;border:none;padding:0}"
                "QPushButton:hover{background:rgba(210,115,138,0.3);border-radius:6px}"
                "QPushButton:pressed{background:rgba(210,115,138,0.5);border-radius:6px}"
            )
            btn.clicked.connect(self._restore_from_tray)
            self._restore_win.hide()

            self.timer = QTimer(self)
            self.timer.timeout.connect(self.read_frames)
            self.timer.start(16)

            self.hotkey_timer = QTimer(self)
            self.hotkey_timer.timeout.connect(self._poll_global_hotkey)
            self.hotkey_timer.start(80)

            # 全局鼠标跟踪：每 50ms 采样鼠标位置，发送到 renderer 驱动 Live2D 视线+身体
            self._pointer_timer = QTimer(self)
            self._pointer_timer.timeout.connect(self._track_pointer)
            self._pointer_timer.start(50)

            self._home_click_timer = QTimer(self)
            self._home_click_timer.setSingleShot(True)
            self._home_click_timer.setInterval(260)
            self._home_click_timer.timeout.connect(self._handle_phone_home_click)

            from PySide6.QtGui import QKeySequence, QShortcut
            # Ctrl+Space 已由 _poll_global_hotkey 的 win32api 全局轮询处理，
            # 此处不再注册 QShortcut，避免重复触发 _focus_input。
            QShortcut(QKeySequence("Escape"), self).activated.connect(
                self._hide_input_or_noop
            )

        def _focus_input(self) -> None:
            self._restore_from_tray()
            if not self.input_panel.isVisible():
                self._toggle_input_panel()
            self.input.setFocus()

        def _toggle_input_panel(self) -> None:
            """切换输入面板：Dock 淡出 + 输入框淡入，或反向。"""
            # 任意方向切换先取消挂起的 hide，避免淡出动画中途再次展开后被强制隐藏
            if self._pending_panel_hide is not None:
                self._pending_panel_hide.stop()
                self._pending_panel_hide = None
            if self.input_panel.isVisible() and self._input_opacity.opacity() > 0.5:
                # 收起 input，恢复 dock 可点击
                self._cross_fade(self._input_opacity, self._dock_opacity)
                self._pending_panel_hide = QTimer.singleShot(200, self.input_panel.hide)
                self.dock_bar.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            else:
                # 展开 input，dock 透明时不拦截鼠标（避免误点 dock 按钮）
                self.input_panel.show()
                self.input.setFocus()
                self._cross_fade(self._dock_opacity, self._input_opacity)
                self.dock_bar.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        def _cross_fade(self, fade_out_effect, fade_in_effect) -> None:
            """200ms opacity 交叉淡入淡出。"""
            anim_out = QPropertyAnimation(fade_out_effect, b"opacity", self)
            anim_out.setDuration(200)
            anim_out.setStartValue(fade_out_effect.opacity())
            anim_out.setEndValue(0.0)
            anim_out.setEasingCurve(QEasingCurve.OutCubic)
            anim_out.start()
            self._fade_out_anim = anim_out

            anim_in = QPropertyAnimation(fade_in_effect, b"opacity", self)
            anim_in.setDuration(200)
            anim_in.setStartValue(fade_in_effect.opacity())
            anim_in.setEndValue(1.0)
            anim_in.setEasingCurve(QEasingCurve.OutCubic)
            anim_in.start()
            self._fade_in_anim = anim_in

        def _toggle_pin(self) -> None:
            """固定/解锁位置。固定后禁用拖拽和自动贴合。v4：DockButton 固定态视觉反馈。"""
            self._pinned = not self._pinned
            self.dock_bar.button("固定").set_pinned(self._pinned)

        def _set_bubble_text(self, text: str) -> None:
            """设置回复气泡文字并自动缩放大小（支持长文本内部滚动）。

            v4：正文走富文本（_wrap_bubble_html：1.5 行距 + 左对齐），
            尺寸用 QTextDocument 估算（QLabel 无法用 QFontMetrics 测行距）。
            名牌/注脚/四角括号/状态行随气泡几何同步。
            """
            bubble_html = _wrap_bubble_html(text)
            self.reply_bubble.setText(bubble_html)
            # 手机屏幕区：x=20 w=264；Dock 顶 y = 118+496-64 = 550
            screen_x, screen_w = 20, 264
            dock_top = 118 + 496 - 64
            w, h = _bubble_size_hint(bubble_html, self.reply_bubble.font(), screen_w - 16)
            w = min(max(w, 80), screen_w - 16)
            h = min(max(h, 36), 100)
            x = screen_x + (screen_w - w) // 2
            y = dock_top - h - 6
            self.reply_bubble.setGeometry(x, y, w, h)
            # 头部名牌/底部注脚/四角括号/状态行跟随气泡几何（fauux 稿⑤⑩④）。
            # 初始化早期调用本方法时配件尚未创建，传 None 由 _sync_bubble_accessories
            # 跳过同步；构建完成后正常跟随。
            _sync_bubble_accessories(
                getattr(self, 'bubble_header', None),
                getattr(self, 'bubble_footer', None),
                getattr(self, 'bubble_corners', None),
                getattr(self, 'status_line', None),
                x, y, w, h,
            )

        def _show_thinking_dots(self) -> None:
            """delta 期间显示等待叙事（fauux 启动序列行 + 多语言短语轮换），气泡呼吸。"""
            # v4：状态行在思考期间隐藏（工具进度由状态行表达，台词区回到 thinking）
            if hasattr(self, 'status_line'):
                self.status_line.hide()
            self._set_bubble_text("> linking fork.db\n… ok")
            # 底部注脚切为相位行（④ ║▒░♫ 与等待叙事同源）
            if getattr(self, 'bubble_footer', None):
                self.bubble_footer.setText("║▒░ ♫ ░▒║ · synchronizing")
            if not hasattr(self, '_bubble_opacity'):
                self._bubble_opacity = QGraphicsOpacityEffect(self.reply_bubble)
                self.reply_bubble.setGraphicsEffect(self._bubble_opacity)
            if not hasattr(self, '_thinking_anim'):
                self._thinking_anim = QPropertyAnimation(self._bubble_opacity, b"opacity", self)
                self._thinking_anim.setDuration(1000)
                self._thinking_anim.setStartValue(0.4)
                self._thinking_anim.setKeyValueAt(0.5, 1.0)
                self._thinking_anim.setEndValue(0.4)
                self._thinking_anim.setLoopCount(-1)
            if self._thinking_anim.state() != QPropertyAnimation.Running:
                self._thinking_anim.start()
            # 多语言短语轮换（fauux [make me sad] 交互）：1.2s 一轮
            if not hasattr(self, '_think_phrase_timer'):
                self._think_phrase_timer = QTimer(self)
                self._think_phrase_timer.setInterval(1200)
                self._think_phrase_timer.timeout.connect(self._cycle_think_phrase)
            self._think_phrase_timer.start()

        def _cycle_think_phrase(self) -> None:
            """轮换 thinking 气泡第二行的状态短语。"""
            phrases = ("[synchronizing mind]", "[思维同步中]", "[心を同期中]", "[make me sad]")
            self._think_phrase_index = getattr(self, '_think_phrase_index', 0)
            self._think_phrase_index = (self._think_phrase_index + 1) % len(phrases)
            if not self.reply_bubble.isVisible():
                return
            self.reply_bubble.setText(_wrap_bubble_html(
                f"> linking fork.db\n… ok　{phrases[self._think_phrase_index]}"
            ))

        def _send_emotion(self, emotion: str) -> None:
            """经 duplex 管道发送 emotion 到 renderer（即时，不等 LLM）。"""
            send_command(emotion=emotion)

        # ===== 通话态切换 =====
        def _toggle_call(self) -> None:
            """Dock 电话按钮：非通话态→进入通话，通话态→挂断。"""
            decision = _decide_call_toggle_action(self._in_call)
            if decision["enter_call"]:
                self._enter_call()
            elif decision["hangup"]:
                self._hangup_call()

        def _enter_call(self) -> None:
            """进入通话态：隐藏平时组件，显示 CallView，启动管线。"""
            config = {**PHONE_DEFAULTS, **load_config()}
            if not all(config.get(key) for key in ("endpoint", "api_key", "model")):
                SettingsDialog(self).exec()
                return
            self._in_call = True
            # 通话独占音频：立即停掉桌面 TTS（避免两个 SpeechPlayer 抢输出流，
            # 以及桌面播报声被通话 VAD 误拾成用户说话）
            try:
                self.speech.stop()
            except Exception:
                pass
            self.reply_bubble.hide()
            self.input_panel.hide()
            self.dock_bar.hide()
            self.call_view.show()
            self.call_view.raise_()
            self.call_view.setFocus()  # 确保 CallView 能接收键盘事件（Escape 退出）
            # 断开旧 controller 信号（避免重复连接）
            if self.call_controller is not None:
                try:
                    self.call_controller.phase_changed.disconnect()
                    self.call_controller.subtitle.disconnect()
                    self.call_controller.elapsed.disconnect()
                    self.call_controller.waveform.disconnect()
                    self.call_controller.you_said.disconnect()
                    self.call_controller.error.disconnect()
                    self.call_controller.muted_changed.disconnect()
                    self.call_controller.screen_share_changed.disconnect()
                    self.call_controller.mouth_intensity.disconnect()
                except Exception:
                    pass
            # 重新创建 controller 以用最新 config
            from core.voice.voice_call import VoiceCallController
            self.call_controller = VoiceCallController(
                config,
                character,
                self,
                on_record=lambda role, content: add_message(
                    active_session(self._state), role, content
                ),
            )
            self.call_controller.phase_changed.connect(self._on_call_phase_changed)
            self.call_controller.subtitle.connect(self.call_view.set_subtitle)
            self.call_controller.reply_show.connect(self.call_view.set_reply_show)
            self.call_controller.elapsed.connect(self.call_view.set_elapsed)
            self.call_controller.waveform.connect(self.call_view.set_waveform)
            self.call_controller.you_said.connect(self._on_call_you_said)
            self.call_controller.error.connect(self._on_call_error)
            self.call_controller.muted_changed.connect(self.call_view.set_muted)
            self.call_controller.screen_share_changed.connect(self.call_view.set_screen_share)
            self.call_controller.mouth_intensity.connect(lambda value: send_command(mouth=value))
            # 断开 CallView 旧信号再重连（防止重复连接导致 hangup 被多次调用）
            try:
                self.call_view.mute_clicked.disconnect()
                self.call_view.hangup_clicked.disconnect()
                self.call_view.screen_clicked.disconnect()
            except Exception:
                pass
            self.call_view.mute_clicked.connect(self.call_controller.toggle_mute)
            self.call_view.hangup_clicked.connect(self._hangup_call)
            self.call_view.screen_clicked.connect(self.call_controller.toggle_screen_share)
            self.call_view.advance_requested.connect(self.call_controller.advance_reply)
            self.call_view.set_muted(self.call_controller.is_muted)
            self.call_view.set_screen_share(self.call_controller.screen_share_on)
            self.call_controller.start()

        def _hangup_call(self) -> None:
            """挂断：停管线，恢复平时态。即使 controller 为 None 也能恢复 UI。"""
            if not self._in_call:
                return
            self._in_call = False
            if self.call_controller is not None:
                try:
                    self.call_controller.hangup()
                except Exception:
                    pass
                try:
                    self.call_controller.phase_changed.disconnect()
                    self.call_controller.subtitle.disconnect()
                    self.call_controller.elapsed.disconnect()
                    self.call_controller.waveform.disconnect()
                    self.call_controller.you_said.disconnect()
                    self.call_controller.error.disconnect()
                    self.call_controller.muted_changed.disconnect()
                    self.call_controller.screen_share_changed.disconnect()
                    self.call_controller.mouth_intensity.disconnect()
                except Exception:
                    pass
                self.call_controller = None
            self.call_view.clear_reply()
            self.call_view.hide()
            self.dock_bar.show()
            self.reply_bubble.show()
            # 恢复对话气泡
            try:
                msgs = active_session(self._state)["messages"]
                if msgs:
                    self._set_bubble_text(self._latest_line(msgs[-1]["content"]))
            except Exception:
                pass

        def _on_call_phase_changed(self, phase: str) -> None:
            self.call_view.set_phase(phase)
            # Live2D 表情随状态切换
            emotion_map = {
                "listening": "neutral",
                "processing": "thinking",
                "speaking": "smile",
                "ended": "neutral",
            }
            emotion = emotion_map.get(phase)
            if emotion:
                self._send_emotion(emotion)
            # speaking 态驱动 Live2D 口型
            if phase == "speaking":
                send_command(speaking=True)
            elif phase in ("listening", "ended"):
                send_command(speaking=False)

        def _on_call_you_said(self, text: str) -> None:
            """通话中识别到的用户话语：常驻显示在 CallView 小字标签。"""
            try:
                self.call_view.set_you_said(text)
            except Exception:
                pass

        def _on_call_error(self, text: str) -> None:
            self.call_view.set_subtitle(f"⚠ {text}")

        def _stop_thinking_anim(self) -> None:
            """停止思考呼吸动画与短语轮换，并恢复气泡不透明度（finished/failed 共用）。"""
            if hasattr(self, '_think_phrase_timer') and self._think_phrase_timer.isActive():
                self._think_phrase_timer.stop()
            if hasattr(self, 'bubble_footer'):
                self.bubble_footer.setText("wire ESTABLISHED · Δ 0.41s · ch 1")
            if hasattr(self, '_thinking_anim') and self._thinking_anim.state() == QPropertyAnimation.Running:
                self._thinking_anim.stop()
                if hasattr(self, '_bubble_opacity'):
                    self._bubble_opacity.setOpacity(1.0)

        def _show_layered_bubbles(self, text: str) -> None:
            """将回复分层后分多个气泡前后展示，每段用 opacity 动画淡入。"""
            self._cancel_bubbles()
            # 停止思考呼吸动画，恢复 opacity
            self._stop_thinking_anim()
            self._thinking_dots_shown = False
            # v4：正式回复到达，状态行隐藏（台词与状态分离）
            if hasattr(self, 'status_line'):
                self.status_line.hide()
            merged = _final_bubble_segments(text)
            if not merged:
                return
            self._bubble_segments = merged
            self._bubble_index = 0
            self.reply_bubble.show()
            self._show_next_bubble()

        def _show_next_bubble(self) -> None:
            """显示下一个气泡分段，用 opacity 动画淡入。"""
            if self._bubble_index >= len(self._bubble_segments):
                self._schedule_bubble_hide()
                return
            text = self._bubble_segments[self._bubble_index]
            self._set_bubble_text(text)
            # opacity 淡入动画
            if not hasattr(self, '_bubble_opacity'):
                self._bubble_opacity = QGraphicsOpacityEffect(self.reply_bubble)
                self.reply_bubble.setGraphicsEffect(self._bubble_opacity)
            self._bubble_opacity.setOpacity(0.0)
            fade_in = QPropertyAnimation(self._bubble_opacity, b"opacity", self)
            fade_in.setDuration(180)
            fade_in.setStartValue(0.0)
            fade_in.setEndValue(1.0)
            fade_in.setEasingCurve(QEasingCurve.OutCubic)
            fade_in.start()
            self._fade_anim = fade_in  # 保持引用防 GC
            self.reply_bubble.show()
            self._bubble_index += 1
            # 全部段展示完后，9 秒无操作再隐藏气泡
            if self._bubble_index >= len(self._bubble_segments):
                self._schedule_bubble_hide()

        def _schedule_bubble_hide(self) -> None:
            """9 秒无操作隐藏气泡。用可取消的 QTimer 实例：
            QTimer.singleShot 静态版无法引用取消，流式期间用户可能仍在
            逐句阅读，新分段到达时旧定时器必须能被 _cancel_bubbles 停掉。"""
            if self._bubble_timer is not None:
                self._bubble_timer.stop()
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._hide_idle_bubble)
            timer.start(9000)
            self._bubble_timer = timer

        def _cancel_bubbles(self) -> None:
            """取消正在进行的气泡序列。"""
            if self._bubble_timer is not None:
                self._bubble_timer.stop()
                self._bubble_timer = None
            self._bubble_segments = []
            self._bubble_index = 0

        def _notify_tts_offline(self) -> None:
            """语音服务离线提示：在当前气泡序列末尾追加一条，不打断展示。"""
            notice = "（语音服务离线）"
            segments = getattr(self, "_bubble_segments", [])
            if notice in segments:
                return
            self._bubble_segments = segments + [notice]
            # 原序列已展示完毕但气泡仍可见时，继续展示追加段（否则提示永不出现）
            if self._bubble_index >= len(self._bubble_segments) - 1 and self.reply_bubble.isVisible():
                self._bubble_index = len(self._bubble_segments) - 1
                self._show_next_bubble()

        def _animate_to(self, target: QPoint) -> None:
            """平滑滑动到目标位置（300ms OutCubic 缓动）。"""
            if self._snap_anim is not None:
                self._snap_anim.stop()
            self._snap_anim = QPropertyAnimation(self, b"pos")
            self._snap_anim.setDuration(300)
            self._snap_anim.setStartValue(self.pos())
            self._snap_anim.setEndValue(target)
            self._snap_anim.setEasingCurve(QEasingCurve.OutCubic)
            self._snap_anim.start()

        def _restore_from_tray(self) -> None:
            self._restore_win.hide()
            self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()

        def _minimize_to_tray(self) -> None:
            self.hide()
            self._restore_win.show()

        def _handle_phone_home_click(self) -> None:
            self._minimize_to_tray()

        @staticmethod
        def _phone_menu_rect() -> QRect:
            return QRect(10, 10, 30, 30)

        @staticmethod
        def _phone_home_rect() -> QRect:
            return QRect(134, 621, 36, 36)

        def _tray_activated(self, reason) -> None:
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
                self._restore_from_tray()

        @staticmethod
        def _latest_line(text: str) -> str:
            """提取气泡显示文本：完整 chinese 部分（多段 === 时合并所有中文段）。

            修复：原版只取最后 3 行 + 105 字截断，长回复被截断。
            现改为返回完整 chinese（去掉 [emotion:] 标签，保留所有中文段，多段用空行分隔）。
            气泡 _set_bubble_text 高度自适应 + 内部滚动支持长文本。
            """
            chinese = parse_reply(text).chinese
            return chinese if chinese.strip() else "…"

        def _terminal_session_lines(self) -> list:
            """当前会话消息 → 终端行（cmd=用户 / out=kurisu 中文）。"""
            lines = []
            for message in active_session(self._state)["messages"]:
                if message["role"] == "assistant":
                    chinese = parse_reply(message["content"]).chinese
                    if chinese.strip():
                        lines.append(("out", chinese))
                else:
                    lines.append(("cmd", message["content"]))
            return lines

        def _position_terminal(self) -> None:
            """终端固定在主窗口左侧，随主窗口移动，不能主动拖动。"""
            if self.terminal is None or not self.terminal.isVisible():
                return
            gap = -24  # 负值：终端右移约一个 dock 图标宽度（32px），贴近角色
            x = self.x() - self.terminal.width() - gap
            y = self.y() + (self.height() - self.terminal.height()) // 2
            screen = QApplication.primaryScreen().availableGeometry()
            x = max(x, screen.left())
            y = max(y, screen.top())
            self.terminal.move(x, y)

        def moveEvent(self, event) -> None:
            super().moveEvent(event)
            self._position_terminal()

        def _terminal_active(self) -> bool:
            return self.terminal is not None and self.terminal.isVisible()

        def _toggle_terminal(self) -> None:
            """Dock 终端按钮：打开/关闭独立 CRT 终端窗口。"""
            if self._terminal_active():
                self.terminal.close()
                return
            if self.terminal is None:
                self.terminal = AgentTerminal(self)
                self.terminal.submitted.connect(self._terminal_send)
                self.terminal.interrupt_requested.connect(self._interrupt_agent)
                self.terminal._history = list(load_terminal_state().get("history") or [])
            # 每次打开都从当前会话重建终端行，避免只加载一次导致
            # 之后主页面新增的聊天记录不显示在终端（历史抽屉是每次重读的）。
            self._term_lines = self._terminal_session_lines()
            self._terminal_reply_index = None
            self.terminal.render_lines(self._term_lines, full=True)
            self.terminal.show()
            self._position_terminal()
            self.terminal.raise_()
            self.terminal.activateWindow()
            self.terminal.input.setFocus()

        def _terminal_command_context(self) -> TerminalCommandContext:
            def list_skills() -> list[tuple[str, str, str]]:
                return [
                    (info.name, info.description, info.source)
                    for info in self._skill_manager.discover().values()
                ]

            def enable_skill(name: str) -> tuple[bool, str]:
                try:
                    skill = self._skill_manager.load(name)
                except KeyError:
                    return False, f"skill not found: {name}"
                except (OSError, UnicodeError, ValueError) as exc:
                    return False, f"skill load failed: {exc}"
                self._active_skills[skill.info.name] = skill
                return True, f"skill enabled: {skill.info.name}"

            def clear_skills() -> None:
                self._active_skills.clear()

            def new_session() -> None:
                create_session(self._state, get_random_greeting(character.id))
                save_state(character.id, self._state)
                self._terminal_session_id = f"amadeus-terminal-{uuid.uuid4().hex}"
                self._term_lines = self._terminal_session_lines()
                self._terminal_reply_index = None

            history = self.terminal._history if self.terminal is not None else []
            return TerminalCommandContext(
                route_mode=self._terminal_route_mode,
                cwd=self._terminal_cwd,
                history=history,
                active_skills=list(self._active_skills.keys()),
                list_skills=list_skills,
                enable_skill=enable_skill,
                clear_skills=clear_skills,
                new_session=new_session,
            )

        def _handle_terminal_command(self, text: str) -> str | None:
            result = terminal_command_registry.dispatch(text, self._terminal_command_context())
            if not result.handled:
                return text
            if result.route_mode is not None:
                self._terminal_route_mode = result.route_mode
            if result.cwd is not None:
                self._terminal_cwd = result.cwd
            save_terminal_state(
                history=self.terminal._history if self.terminal is not None else [],
                route=self._terminal_route_mode,
                cwd=self._terminal_cwd,
                session_id=self._terminal_session_id,
            )
            if result.clear:
                self._term_lines = []
                self._terminal_reply_index = None
            self._term_lines.extend(result.lines)
            if self.terminal is not None:
                self.terminal.render_lines(self._term_lines, full=result.clear)
            return result.forward_text

        def _terminal_backend_ready(self) -> bool:
            """Check only the credentials needed by the selected terminal route."""
            config = load_config()
            local_ready = all(config.get(key) for key in ("endpoint", "api_key", "model"))
            if self._terminal_route_mode == "local":
                return local_ready
            harness = dict((config.get("agent_router") or {}).get("harness") or {})
            harness.update(config.get("harness") or {})
            harness_ready = bool(
                (harness.get("api_key") or config.get("api_key"))
                and (harness.get("model") or config.get("model") or "deepseek-v4-flash")
            )
            return local_ready or harness_ready

        def _interrupt_agent(self) -> None:
            task = self._active_agent_task
            if task is None or not self._busy:
                if self.terminal is not None:
                    self.terminal._lines.append(("sys", "^C — 当前没有正在运行的任务"))
                    self.terminal.render_lines(self.terminal._lines)
                return
            task.cancel()
            try:
                from core.llm.harness_bridge import cancel_active_run
                cancel_active_run()
            except Exception:
                pass
            if self.terminal is not None:
                self.terminal._lines.append(("sys", "^C — 已请求中断当前任务"))
                self.terminal.render_lines(self.terminal._lines)

        def _terminal_route_override(self) -> str:
            if self._terminal_route_mode == "local":
                return "local"
            if self._terminal_route_mode == "harness":
                return "harness"
            return "terminal_auto"

        def _terminal_skill_prompt(self) -> str:
            return build_skill_prompt(list(self._active_skills.values()))

        def _terminal_send(self, text: str) -> None:
            """终端提交：复用气泡发送管线（不显示气泡，输出回显到终端）。"""
            if self._busy:
                return
            forwarded = self._handle_terminal_command(text)
            if forwarded is None:
                return
            text = forwarded
            save_terminal_state(
                history=self.terminal._history if self.terminal is not None else [],
                route=self._terminal_route_mode,
                cwd=self._terminal_cwd,
                session_id=self._terminal_session_id,
            )
            if not self._terminal_backend_ready():
                SettingsDialog(self).exec()
                return
            self._term_lines.append(("cmd", text))
            self._term_lines.append(("out", "▌"))
            self._terminal_reply_index = len(self._term_lines) - 1
            self.terminal.render_lines(self._term_lines)
            self._send_text(
                text,
                show_bubble=False,
                route_override=self._terminal_route_override(),
                response_max_tokens=None,
                inject_system_prompt=self._terminal_skill_prompt(),
                terminal_cwd=str(self._terminal_cwd),
                terminal_session_id=self._terminal_session_id,
            )

        def _hide_input_or_noop(self) -> None:
            """Escape 键：收起输入面板（若已展开），Dock 淡入恢复。"""
            if self.input_panel.isVisible() and self._input_opacity.opacity() > 0.5:
                self._toggle_input_panel()

        def _poll_global_hotkey(self) -> None:
            try:
                import win32api
            except ImportError:
                return  # 非 Windows 平台，跳过全局热键
            # Ctrl+Space → 聚焦输入
            cs_pressed = bool(win32api.GetAsyncKeyState(0x11) & 0x8000) and bool(win32api.GetAsyncKeyState(0x20) & 0x8000)
            if cs_pressed and not self._hotkey_down:
                self._focus_input()
            self._hotkey_down = cs_pressed
            # Ctrl+Alt+S → 切换显示/隐藏
            cas_pressed = (
                bool(win32api.GetAsyncKeyState(0x11) & 0x8000)
                and bool(win32api.GetAsyncKeyState(0x12) & 0x8000)
                and bool(win32api.GetAsyncKeyState(0x53) & 0x8000)
            )
            if cas_pressed and not self._csa_down:
                self._toggle_visibility()
            self._csa_down = cas_pressed

        def _toggle_visibility(self) -> None:
            """Ctrl+Alt+S 切换桌宠显示/隐藏。"""
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.raise_()
                self.activateWindow()

        def _track_pointer(self) -> None:
            """全局鼠标跟踪：采样光标位置，归一化后发送到 renderer 驱动 Live2D 视线+身体。

            归一化逻辑：以窗口中心为原点，水平 ±250px / 垂直 ±200px 映射到 [-1, 1]，
            clamp 后发送。鼠标在窗口左侧 → pointerX = -1（角色看左），右侧 → +1。
            光标静止时跳过发送（管道去重）：renderer 收不到 pointer 更新即视为
            无鼠标活动，作为闲置微动作计时器的复位信号（live2d_page.html setPointer）。
            """
            try:
                cursor = QCursor.pos()
                center = self.geometry().center()
                dx = (cursor.x() - center.x()) / 250.0
                dy = (cursor.y() - center.y()) / 200.0
                px = max(-1.0, min(1.0, dx))
                py = max(-1.0, min(1.0, dy))
                last = getattr(self, "_last_pointer", None)
                if last is not None and abs(px - last[0]) < 1e-3 and abs(py - last[1]) < 1e-3:
                    return
                self._last_pointer = (px, py)
                send_command(pointer=(px, py))
            except Exception:
                pass

        def _send(self) -> None:
            text = self.input.text().strip()
            if not text or self._busy:
                return
            self._send_text(text)

        def _send_text(
            self,
            text: str,
            show_bubble: bool = True,
            route_override: str | None = None,
            response_max_tokens: int | None = 700,
            inject_system_prompt: str | None = None,
            terminal_cwd: str | None = None,
        ) -> None:
            """发送核心：气泡与终端共用（终端模式不显示气泡，回显走终端）。"""
            config = load_config()
            if route_override == "harness" or route_override == "terminal_auto":
                config_ready = self._terminal_backend_ready()
            else:
                config_ready = all(config.get(key) for key in ("endpoint", "api_key", "model"))
            if not config_ready:
                SettingsDialog(self).exec()
                return
            self.input.clear()
            self._cancel_bubbles()
            if show_bubble:
                self.reply_bubble.show()
            session = active_session(self._state)
            add_message(session, "user", text)
            instant = _decide_send_instant_action()
            if show_bubble:
                self._show_thinking_dots()
            self._send_emotion(instant["emotion"])
            self._busy = True
            if self.terminal is not None:
                self.terminal.set_busy(True)
            self._streamed_reply = ""
            # 流式 TTS 状态：_stream_sep_count 记录已遇到的 === 数（奇数=日语段，偶数=中文段）
            # _stream_tts_started 标记是否已启动 speak_streaming_start（避免重复启动）
            # 修复 LLM 输出多段 === 交替（中文===日语===中文===日语）时把中文段也送 TTS 的 bug
            self._stream_sep_count = 0
            self._stream_tts_started = False
            # 流式气泡上屏节流状态（60ms 一帧）；_stream_reading 标记用户是否
            # 已在流式期间单击进入逐句阅读（进入后流式文字不再覆盖当前句），
            # _stream_live 标记是否已点完所有完成句（追平后尾巴继续打字机）
            self._stream_reading = False
            self._stream_live = False
            self._stream_paint_ts = 0.0
            self._stream_paint_text = ""
            self.send_button.setDisabled(True)
            history = [{"role": message["role"], "content": message["content"]} for message in session["messages"][-14:]]
            if route_override is None:
                route_override = "local"
            if response_max_tokens == 700:
                router_cfg = config.get("agent_router") or {}
                try:
                    response_max_tokens = int(router_cfg.get("chat_max_tokens", 700))
                except (TypeError, ValueError):
                    response_max_tokens = 700
            task = AgentTask(
                history,
                session.get("memories", []),
                character=character,
                route_override=route_override,
                response_max_tokens=response_max_tokens,
                inject_system_prompt=inject_system_prompt,
                terminal_cwd=terminal_cwd,
            )
            self._active_agent_task = task
            task.signals.status.connect(self._show_status)
            task.signals.delta.connect(self._agent_delta)
            task.signals.finished.connect(self._agent_finished)
            task.signals.cancelled.connect(self._agent_cancelled)
            task.signals.failed.connect(self._agent_failed)
            task.signals.tool_event.connect(self._agent_tool_event)
            task.signals.confirmation.connect(self._confirm_operation)
            QThreadPool.globalInstance().start(task)

        def _agent_cancelled(self, partial_reply: str) -> None:
            self._busy = False
            self._streamed_reply = ""
            self._active_agent_task = None
            self._stop_thinking_anim()
            if self.terminal is not None:
                self.terminal.set_busy(False)
                if (
                    self._terminal_reply_index is not None
                    and self._terminal_reply_index < len(self._term_lines)
                ):
                    self._term_lines[self._terminal_reply_index] = ("sys", "^C — 任务已中断")
                    self.terminal.render_lines(self._term_lines, dirty_from=self._terminal_reply_index)
            self._terminal_reply_index = None
            self.send_button.setDisabled(False)

        def _confirm_operation(self, request: dict) -> None:
            if self._terminal_active():
                self._term_lines.append(("sys", "等待工具审批…"))
                self.terminal.render_lines(self._term_lines)
                self.terminal.request_approval(request)
                return
            payload = request["payload"]
            command = payload.get("command", "")
            description = payload.get("description", "")
            detail_text = description or command
            if not detail_text:
                detail_text = __import__("json").dumps(payload, ensure_ascii=False, indent=2)
            details = html.escape(detail_text)
            msg = QMessageBox(self)
            msg.setWindowTitle("红莉栖请求操作电脑")
            msg.setIcon(QMessageBox.Question)
            msg.setText(f"她要执行：\n{details}\n\n允许吗？")
            btn_once = msg.addButton("仅本次", QMessageBox.AcceptRole)
            btn_session = msg.addButton("本次会话", QMessageBox.AcceptRole)
            btn_always = msg.addButton("始终允许", QMessageBox.AcceptRole)
            btn_deny = msg.addButton("拒绝", QMessageBox.RejectRole)
            msg.setDefaultButton(btn_deny)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == btn_once:
                request["choice"] = "once"
            elif clicked == btn_session:
                request["choice"] = "session"
            elif clicked == btn_always:
                request["choice"] = "always"
            else:
                request["choice"] = "deny"
            request["event"].set()

        def _agent_tool_event(self, event: dict) -> None:
            if not self._terminal_active():
                return
            kind = event.get("kind")
            name = event.get("name", "tool")
            if kind == "tool_call":
                args = event.get("arguments") or {}
                if name == "str_replace_editor":
                    command = str(args.get("command", ""))
                    self._term_lines.append(("tool", f"{name} {command} {args.get('path', '')}".rstrip()))
                elif name in ("bash", "pwsh", "run_bash", "run_command"):
                    cmd = args.get("command") or args.get("cmd") or ""
                    self._term_lines.append(("tool", f"{name} $ {cmd}" if cmd else name))
                else:
                    self._term_lines.append(("tool", f"{name} {_tool_args_summary(args)}".rstrip()))
            elif kind == "tool_result":
                content = str(event.get("content", "") or "")
                is_error = bool(event.get("isError", False))
                args = event.get("arguments") or {}
                command = str(args.get("command", ""))
                if is_error:
                    self._term_lines.append(("err", content or f"{name} 执行失败"))
                elif name == "str_replace_editor" and command in ("create", "str_replace", "insert"):
                    self._term_lines.append(("diff", "", _editor_diff_extra(args)))
                elif content:
                    self._term_lines.append(("result", content))
                else:
                    self._term_lines.append(("tool", f"✓ {name}"))
            self.terminal.render_lines(self._term_lines)

        def _show_status(self, text: str) -> None:
            # v4：状态与台词分离 —— 工具进度走独立 dim 状态行，不覆盖台词气泡
            if not self._terminal_active():
                if hasattr(self, 'status_line'):
                    self.status_line.setText("▸ " + text)
                    self.status_line.show()

        def _agent_delta(self, text: str) -> None:
            new_streamed, should_show_thinking, should_set_bubble_text = _decide_delta_action(
                self._streamed_reply, text, self._terminal_active()
            )
            self._streamed_reply = new_streamed
            if (
                self._terminal_active()
                and self._terminal_reply_index is not None
                and self._terminal_reply_index < len(self._term_lines)
            ):
                # 终端模式：流式中文实时回显到最后一行 out
                chinese = parse_reply(self._streamed_reply).chinese
                self._term_lines[self._terminal_reply_index] = ("out", chinese if chinese.strip() else "▌")
                self.terminal.render_lines(self._term_lines, dirty_from=self._terminal_reply_index)
            if should_set_bubble_text:
                # 流式气泡：首字即上屏，不等 finished（本地直连低延迟体感优化）。
                # 停掉思考呼吸动画与 1.2s 短语轮换，防止轮换把流式文字覆盖掉。
                self._stop_thinking_anim()
                display = _streamed_display_text(self._streamed_reply)
                # 增量分句：句末标点一到位即成为可单击的分段，不必等整条回复
                # 结束（TTS 逐句播放时第一段声音未完也能推进阅读）
                self._bubble_segments, tail = _split_stream_segments(display)
                now = time.monotonic()
                if not getattr(self, "_stream_reading", False):
                    # 未进入逐句阅读：整段打字机预览（原有行为）
                    paint_text = display
                elif getattr(self, "_stream_live", False) or self._bubble_index >= len(self._bubble_segments):
                    # 已追平全部完成句：只打字机当前残句（新句完成后仍保持直播）
                    paint_text = tail
                    self._stream_live = True
                else:
                    # 用户停在已完成的句子上：保持不动，等下一次单击推进
                    paint_text = None
                # 本地模型 delta 可达数百次/秒，节流到 ~16fps 防止布局风暴；
                # 尾帧由 _agent_finished 的完整文本兜底，丢帧无碍
                if paint_text and paint_text != getattr(self, "_stream_paint_text", None) and (
                    now - getattr(self, "_stream_paint_ts", 0.0) >= 0.06
                ):
                    self._stream_paint_ts = now
                    self._stream_paint_text = paint_text
                    self._set_bubble_text(paint_text + "▌")
            if should_show_thinking:
                self._show_thinking_dots()
            # 流式 TTS：纯 === 切段 + 日语字符过滤（修复多段 === + emotion 错位 bug）
            # LLM 实际输出格式（dist/data/sessions.json 实测）：
            #   中文1
            #   ===
            #   日语1 \n\n [emotion:neutral]中文2     ← [emotion] 在 === 后，与日语1 同段
            #   ===
            #   日语2 \n\n [emotion:neutral]中文3
            #   ===
            #   日语3 \n\n 中文4（无 emotion）
            #   ===
            #   日语4
            # 用 === 切所有段，对每段用 has_japanese() 判断：
            #   - 含假名（平假名/片假名）→ 日语段，提取假名部分送 TTS
            #   - 纯汉字/无假名 → 中文段，跳过
            # 数学本质：日语必有假名（U+3040-309F 平假名 + U+30A0-30FF 片假名），
            # CJK 汉字中日韩共用手写汉字（U+4E00-9FFF）无法区分，但日语段必含假名。
            # 形象理解：每段用 === 切开后扫假名，有假名的是日语段送 TTS。
            # 通话中桌面 TTS 静默：声音统一归通话管线（ctrl._tts），避免两个
            # SpeechPlayer 同时开输出流互抢 + 声音回流被通话 VAD 误拾
            if getattr(self, "_in_call", False):
                return
            config = load_config()
            if not config.get("tts_enabled", True):
                return
            if not text:
                return
            # 把 delta 按 === 切分：parts[0] 是当前段尾部，parts[1:] 是新切段开头
            parts = text.split("===")
            if len(parts) == 1:
                # 无新 ===：当前段增量追加（按假名判断是否送 TTS）
                self._append_tts_segment_by_japanese(parts[0])
            else:
                # 有新 ===：先处理当前段尾部，再逐个切段
                self._append_tts_segment_by_japanese(parts[0])
                for i in range(1, len(parts)):
                    seg = parts[i].lstrip("=\r\n")
                    self._append_tts_segment_by_japanese(seg)

        def _append_tts_segment_by_japanese(self, text: str) -> None:
            """按假名判断是否追加 TTS：含假名的是日语段，提取假名段送 TTS。

            LLM 把日语 + 中文混在同一段（如「日语1 \\n\\n [emotion:neutral]中文2」），
            用策略：先按 [emotion:xxx] 标签切分（去掉标签），再按空白行切段，
            对每段用 has_japanese() 判断含假名则送 TTS，跳过纯中文段。
            """
            if not text:
                return
            import re
            # 去掉 [emotion:xxx] 标签
            cleaned = re.sub(r"\[emotion:[^\]]+\]", "", text)
            # 按空白行（\\n\\n 或 \\n）切段
            chunks = re.split(r"\n\s*\n", cleaned)
            for chunk in chunks:
                segment = chunk.strip()
                if not segment:
                    continue
                # 必须含至少 1 个假名才算日语段
                if not re.search(r"[\u3040-\u309F\u30A0-\u30FF]", segment):
                    continue
                # 首次进入日语段时启动流式 TTS 会话
                if not self._stream_tts_started:
                    config = load_config()
                    self.speech.set_rate([-2, 0, 2][config.get("tts_rate", 1)])
                    self.speech.speak_streaming_start(text_lang="ja")
                    self._stream_tts_started = True
                self.speech.speak_streaming_append(segment)

        def _agent_finished(self, reply: str) -> None:
            session = active_session(self._state)
            add_message(session, "assistant", reply)
            parsed = parse_reply(reply)
            self._busy = False
            self._active_agent_task = None
            self._streamed_reply = ""
            self.send_button.setDisabled(False)
            if self.terminal is not None:
                self.terminal.set_busy(False)
            if (
                self._terminal_active()
                and self._terminal_reply_index is not None
                and self._terminal_reply_index < len(self._term_lines)
            ):
                self._term_lines[self._terminal_reply_index] = ("out", parsed.chinese)
                self.terminal.render_lines(self._term_lines, dirty_from=self._terminal_reply_index)
            self._terminal_reply_index = None
            # 响应提速：TTS 收尾 / 本地 Ollama 表情分类（最长阻塞 3s）/ 会话写盘
            # 全部后移，不再拖慢首屏文字
            if not self._terminal_active():
                # 分层气泡需要完整中文做分段展示，_latest_line 的 105 字截断会丢段
                if getattr(self, "_stream_reading", False) and self._bubble_index > 0:
                    # 流式期间用户已在逐句阅读：同步最终分段但保留当前进度，
                    # 不回卷第一句重播（回卷会让已读过的句子再看一遍）
                    self._bubble_segments = _final_bubble_segments(parsed.chinese)
                    if getattr(self, "_stream_live", False) or self._bubble_index >= len(self._bubble_segments):
                        # 已追平：补显最后一段（流式尾巴此刻刚好完结成句）
                        self._show_next_bubble()
                    # 否则停在当前句，单击继续推进
                else:
                    # 流式期间未点击阅读：整条回复分层展示，从第一句开始
                    self._show_layered_bubbles(parsed.chinese)
                self._stream_reading = False
                self._stream_live = False
            print(f"[PET-DBG] finished reply_len={len(reply)} jp_len={len(parsed.japanese)} tts_started={self._stream_tts_started} sep_count={self._stream_sep_count}")
            print(f"[PET-DBG] finished reply={reply[:200]!r}")
            print(f"[PET-DBG] finished parsed.jp={parsed.japanese[:200]!r}")
            # 流式 TTS：会话结束，刷新剩余缓冲
            # （流式合成在 _agent_delta 中已开始，这里只刷新剩余文本）
            # 通话中桌面 TTS 静默：正在播的停掉、兜底合成跳过（声音归通话管线）
            config = load_config()
            in_call = getattr(self, "_in_call", False)
            if in_call:
                self._stream_tts_started = False
                self.speech.stop()
            elif config.get("tts_enabled", True) and self._stream_tts_started:
                self.speech.speak_streaming_end()
            elif config.get("tts_enabled", True) and parsed.japanese and not self._stream_tts_started:
                # 兜底：如果流式未启动（如无 === 分隔符），整段合成
                print(f"[PET-DBG] 兜底整段合成 jp={parsed.japanese[:60]!r}")
                self.speech.set_rate([-2, 0, 2][config.get("tts_rate", 1)])
                self.speech.speak_with_options(
                    parsed.japanese,
                    text_lang="ja",
                    allow_fallback=False,
                )
            self._stream_sep_count = 0
            self._stream_tts_started = False
            # 表情/动作综合判定：本地 Ollama 小模型分类（emotion+motion），
            # 失败回退规则解析；任何异常回退 parse_reply 的 emotion（表现层不影响主流程）。
            # 放在气泡显示之后执行——分类请求最长阻塞 3s，不应拖慢文字首屏
            try:
                from core.companion.expression import decide_expression
                _cfg = load_config()
                _expr = decide_expression(
                    reply, ollama=(_cfg.get("agent_router") or {}).get("ollama")
                )
                emotion, motion = _expr.emotion, _expr.motion
            except Exception:
                emotion, motion = parsed.emotion, "neutral"
            send_command(emotion=emotion, motion=motion)
            # 会话持久化后移（写盘不阻塞首屏）
            save_state(character.id, self._state)

        def _hide_idle_bubble(self) -> None:
            if not self._busy:
                self.reply_bubble.hide()

        def _agent_failed(self, error: str) -> None:
            self._busy = False
            self._active_agent_task = None
            self._streamed_reply = ""
            self._stream_reading = False
            self._stream_live = False
            # 停止思考呼吸动画，避免失败后气泡永远显示 "● ● ●" 呼吸
            self._stop_thinking_anim()
            if self.terminal is not None:
                self.terminal.set_busy(False)
            if (
                self._terminal_active()
                and self._terminal_reply_index is not None
                and self._terminal_reply_index < len(self._term_lines)
            ):
                self._term_lines[self._terminal_reply_index] = ("err", f"任务失败：{error}")
                self.terminal.render_lines(self._term_lines, dirty_from=self._terminal_reply_index)
            else:
                if hasattr(self, 'status_line'):
                    self.status_line.hide()
                self.reply_bubble.show()
                self._set_bubble_text(self._latest_line(f"任务失败：{error}"))
                # 失败提示也会闲置后隐藏，避免永久悬挂
                self._schedule_bubble_hide()
            self._terminal_reply_index = None
            self.send_button.setDisabled(False)
            send_command(emotion="angry")

        def read_frames(self) -> None:
            latest = None
            while connection.poll():
                kind, payload = connection.recv()
                if kind == "frame":
                    latest = payload
                elif kind == "error":
                    print(f"[Amadeus] {payload}", file=sys.stderr)
                    _write_runtime_log("renderer-error.log", str(payload))
                    if hasattr(self, 'status_line'):
                        self.status_line.hide()
                    self.reply_bubble.show()
                    self._set_bubble_text(
                        "Live2D 渲染启动失败。\n"
                        "请安装 Microsoft Edge WebView2 Runtime，"
                        "或把 data/logs/renderer-crash.log 发给开发者。"
                    )
                elif kind == "home_click":
                    # JS 端 Home 键点击 → 最小化到托盘（保留托盘）
                    self._minimize_to_tray()
                elif kind == "hide_window":
                    # JS 端 Home 键双击 → 隐藏整个窗口，保留托盘
                    self.hide()
                    self._restore_win.show()
                elif kind == "close":
                    # JS 端 × 菜单 → 主动退出（等同于托盘菜单的退出）
                    QApplication.instance().quit()
            if latest is not None:
                image = QImage.fromData(latest, "PNG")
                if not image.isNull():
                    self._frame = image
                    if not self._first_frame_received:
                        self._first_frame_received = True
                        try:
                            (ROOT / "data").mkdir(parents=True, exist_ok=True)
                            image.save(str(ROOT / "data" / "received-frame.png"), "PNG")
                            READY_FILE.parent.mkdir(parents=True, exist_ok=True)
                            READY_FILE.write_text("KURISU_READY", encoding="ascii")
                        except OSError:
                            pass  # 快照/就绪标记仅调试用途，frozen 只读环境失败不影响主流程
                    self.update()
            elif (
                not self._first_frame_received
                and not renderer.is_alive()
                and not getattr(self, "_renderer_exit_reported", False)
            ):
                self._renderer_exit_reported = True
                _write_runtime_log(
                    "renderer-error.log",
                    f"Renderer exited before first frame. exitcode={renderer.exitcode}",
                )
                if hasattr(self, 'status_line'):
                    self.status_line.hide()
                self.reply_bubble.show()
                self._set_bubble_text(
                    "Live2D 渲染进程提前退出。\n"
                    "请安装 Microsoft Edge WebView2 Runtime，"
                    "或查看 data/logs/renderer-error.log。"
                )

        def paintEvent(self, event) -> None:
            if self._frame.isNull():
                return
            painter = QPainter(self)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            # 合成帧尺寸 = 304×690（手机壳 + Live2D 一体，PyWeb 端已画完）
            # 直接按 1:1 贴到窗口，保持透明背景
            target = QRect(0, 0, self.width(), self.height())
            if self._frame.width() == self.width() and self._frame.height() == self.height():
                painter.drawImage(target, self._frame)
            else:
                scaled = self._frame.scaled(
                    self.width(), self.height(),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                painter.drawImage(
                    (self.width() - scaled.width()) // 2,
                    (self.height() - scaled.height()) // 2,
                    scaled,
                )

        def wheelEvent(self, event) -> None:
            if self._pinned:
                return
            delta = event.angleDelta().y() / 120
            new_zoom = self._zoom + delta * 0.1
            self._zoom = max(0.5, min(2.0, new_zoom))
            self.update()

        def eventFilter(self, obj, event) -> bool:
            # 气泡的头部名牌/底部注脚/四角括号/状态行跟随气泡显示状态（fauux 稿⑤⑩④）
            if obj is self.reply_bubble and event.type() in (QEvent.Show, QEvent.Hide):
                vis = event.type() == QEvent.Show
                if hasattr(self, 'bubble_header'):
                    self.bubble_header.setVisible(vis)
                    self.bubble_footer.setVisible(vis)
                    for lbl in self.bubble_corners:
                        lbl.setVisible(vis)
                    if not vis:
                        self.status_line.hide()
            return super().eventFilter(obj, event)

        def _relayout(self) -> None:
            """根据手机屏幕布局重新定位所有组件。
            手机屏幕区域（与 phone_live2d_page.html 对齐）：
              手机框  x=12 y=110 w=280 h=560 （2:1 比例）
              屏幕    x=20 y=118 w=264 h=496 （内缩 8px，底部留 56px Home 键）
            """
            # Dock：屏幕内底部槽位（居中）——角色合成帧已留 64px Dock 预留，不重叠
            dock_w = self.dock_bar.sizeHint().width()
            dock_w = min(dock_w, 250)
            dock_x = 20 + (264 - dock_w) // 2
            dock_y = 118 + 496 - 64
            self.dock_bar.setGeometry(dock_x, dock_y, dock_w, 56)
            # 输入面板：同位互斥
            panel_w = 248
            panel_x = 20 + (264 - panel_w) // 2
            panel_y = dock_y + 2
            self.input_panel.setGeometry(panel_x, panel_y, panel_w, 52)
            # CRT overlay 跟随 input_panel
            if hasattr(self, '_panel_crt') and self._panel_crt is not None:
                self._panel_crt.setGeometry(self.input_panel.rect())
            # 手机屏幕顶部状态栏：屏幕区顶部（y=118，高 26px）
            self.status_bar.setGeometry(20, 118, 264, 26)
            self.status_bar.raise_()
            # 历史抽屉：屏幕内右侧（保持 __init__ 初始位置）
            # 通话视图：覆盖整个屏幕区域
            self.call_view.setGeometry(20, 118, 264, 496)

        def resizeEvent(self, event) -> None:
            self._relayout()
            super().resizeEvent(event)

        def keyPressEvent(self, event: QKeyEvent) -> None:
            if event.key() == Qt.Key_Escape and self._in_call:
                self._hangup_call()
                return
            super().keyPressEvent(event)

        def mousePressEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.LeftButton:
                pos = event.position().toPoint()
                if self._phone_menu_rect().contains(pos):
                    QApplication.instance().quit()
                    event.accept()
                    return
                if self._phone_home_rect().contains(pos):
                    self._home_click_timer.start()
                    event.accept()
                    return
                # 气泡还有剩余分段时，左键点击推进下一句。
                # 必须放在"收起输入面板"之前：发完消息面板仍展开，若先收面板，
                # 用户看下一句的第一击会被吃掉（表现为"点了没反应"）。
                # 流式期间即可点击（分段随句末标点增量生成，不等整条回复）
                if self._bubble_segments and self._bubble_index < len(self._bubble_segments):
                    self._stream_reading = True
                    self._show_next_bubble()
                    # 点完若已到队尾 = 追平，后续流式走"尾巴打字机"直播
                    self._stream_live = (self._bubble_index >= len(self._bubble_segments))
                    return
                # input 可见时点 PetWindow 空白区收起 input，不触发拖拽/聚焦
                if self.input_panel.isVisible() and self._input_opacity.opacity() > 0.5:
                    self._toggle_input_panel()
                    return
                if event.position().x() > 50:
                    self._focus_input()
                if not self._pinned:
                    self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            elif event.button() == Qt.RightButton:
                menu = QMenu(self)
                quit_action = menu.addAction("退出桌宠")
                if menu.exec(event.globalPosition().toPoint()) is quit_action:
                    QApplication.quit()

        def mouseMoveEvent(self, event: QMouseEvent) -> None:
            if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag_offset)

        def mouseReleaseEvent(self, event: QMouseEvent) -> None:
            if self._drag_offset is not None:
                self._user_pos = self.pos()  # 记住用户拖拽后的位置
                self._was_desktop = True
            self._drag_offset = None

        def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.LeftButton and self._phone_home_rect().contains(event.position().toPoint()):
                self._home_click_timer.stop()
                self.hide()
                self._restore_win.show()
                event.accept()
                return

    app = QApplication(sys.argv)
    app.setApplicationName("Amadeus Kurisu")
    _ensure_dither_texture()  # fauux 抖动纹理（幂等，缺失时生成）
    pet = PetWindow()
    app.aboutToQuit.connect(lambda: renderer.terminate() if renderer.is_alive() else None)
    pet.show()
    pet.raise_()

    # === Companion 主动问候子系统 ===
    # 在 PetWindow 实例化后接入（pet 实例已可用，pet._agent_delta / pet._show_status
    # 是 PetWindow 的方法，可复用为 companion 回复的流式表达回调）。
    # PySide6 延迟导入（此处已在 run_overlay 内部，但显式导入让依赖清晰）。
    from core.companion.controller import CompanionController
    from core.companion.sensors import (
        ActiveWindowSensor, ActivityTracker, IdleStateTracker,
        ClipboardSensor, ScreenSensor, build_snapshot,
    )
    from core.companion import storage as companion_storage
    from datetime import datetime as _dt_datetime

    _companion_cfg_full = load_config()
    companion_cfg = {**{"enabled": True, "frequency": "mid", "daily_limit": 30,
                        "quiet_hours": {"start": "23:00", "end": "08:00"},
                        "sensors": {"active_window": True, "activity": True,
                                    "idle": True, "clipboard": False, "screen": False}},
                     **(_companion_cfg_full.get("companion") or {})}
    sensors_cfg = companion_cfg.get("sensors", {})

    aw_sensor = ActiveWindowSensor(interval_seconds=2)
    at_sensor = ActivityTracker(interval_seconds=30)
    it_tracker = IdleStateTracker()
    clip_sensor = ClipboardSensor(interval_seconds=1, enabled=bool(sensors_cfg.get("clipboard", False)))
    screen_sensor = ScreenSensor(enabled=bool(sensors_cfg.get("screen", False)))

    companion_ctrl = CompanionController(
        config=companion_cfg,
        llm_config={
            "endpoint": _companion_cfg_full.get("endpoint", ""),
            "api_key": _companion_cfg_full.get("api_key", ""),
            "model": _companion_cfg_full.get("model", ""),
        },
    )

    def _companion_on_delta(text: str) -> None:
        """companion 回复流式 delta，复用 PetWindow._agent_delta。"""
        try:
            pet._agent_delta(text)
        except Exception:
            pass

    def _companion_on_status(text: str) -> None:
        """companion 状态文本，复用 PetWindow._show_status。"""
        try:
            pet._show_status(text)
        except Exception:
            pass

    # companion 回复的 Live2D 表现指令缓存：controller 在 on_finished 前回调
    last_expression = None

    def _companion_on_expression(expr) -> None:
        """companion 回复 → Live2D 表情/动作（本地小模型 + 规则综合判定）。"""
        nonlocal last_expression
        last_expression = expr
        try:
            if expr.motion:
                send_command(emotion=expr.emotion, motion=expr.motion)
        except Exception:
            pass

    def _companion_on_finished(reply: str) -> None:
        """companion 完整回复落到桌宠气泡，不写入聊天历史。

        C-01 修复：companion 内部情绪（idle/sleepy/concern/tease）通过
        COMPANION_TO_LIVE2D_EMOTION 映射为 Live2D 可识别的情绪标签。
        C-02 修复：同时发送 motion 命令驱动 Live2D 动作（歪头/点头/前倾等），
        让角色不只变表情，还有身体动作，更像真人。
        """
        try:
            from core.companion.prompts import COMPANION_TO_LIVE2D_EMOTION, COMPANION_EMOTION_MOTION
            parsed = parse_reply(reply)
            # 优先用 controller 的 on_expression 结果（本地小模型+规则综合判定），
            # 未注入时回退到 companion 内部情绪映射（C-01/C-02 逻辑不变）
            if last_expression is not None:
                live2d_emotion = last_expression.emotion
                motion = last_expression.motion or COMPANION_EMOTION_MOTION.get(parsed.emotion, "neutral")
            else:
                live2d_emotion = COMPANION_TO_LIVE2D_EMOTION.get(parsed.emotion, "neutral")
                motion = COMPANION_EMOTION_MOTION.get(parsed.emotion, "neutral")
            send_command(emotion=live2d_emotion, motion=motion)
            pet._streamed_reply = ""
            pet._stream_tts_started = False
            pet._show_layered_bubbles(parsed.chinese)
        except Exception:
            pass

    def _companion_last_topic() -> str | None:
        """取最近 2 小时内最新的话题，避免重复问候同一话题（C-08）。"""
        try:
            topics = companion_storage.recent_topics(hours=2)
            return next(iter(topics)) if topics else None
        except Exception:
            return None

    def _companion_tick() -> None:
        """周期性检查 companion 触发（每 30s 一次）。"""
        if not companion_cfg.get("enabled"):
            return
        # 通话中不主动问候：companion 走桌面 SpeechPlayer 播 TTS，会与通话
        # 管线抢输出流、其声音还会被通话 VAD 当成用户说话拾进去
        if getattr(pet, "_in_call", False):
            return
        try:
            aw_sensor._poll()
            at_sensor._poll()
            it_tracker.update(at_sensor.idle_seconds)
            now = _dt_datetime.now()
            local_time = now.strftime("%H:%M 周%w")
            is_deep_night = 23 <= now.hour or now.hour < 6
            snap = build_snapshot(
                active_window=aw_sensor, activity=at_sensor, idle=it_tracker,
                clipboard=clip_sensor, screen=screen_sensor,
                last_greeting_ts=companion_storage.last_greeting_ts(),
                last_topic=_companion_last_topic(),  # C-08 修复：从 storage 取最近话题
                greeting_count=companion_storage.greeting_count_today(),
                local_time=local_time, is_deep_night=is_deep_night,
            )
            companion_ctrl.handle_signal(
                snap, local_hour=now.hour + now.minute / 60,
                on_delta=_companion_on_delta, on_status=_companion_on_status,
                on_finished=_companion_on_finished,
                on_expression=_companion_on_expression,
            )
        except Exception:
            pass  # companion 永不影响主流程

    companion_timer = QTimer(pet)
    companion_timer.timeout.connect(_companion_tick)
    companion_timer.start(30000)  # 30s 周期

    # 启动各传感器独立 QTimer（parent=pet 保证随 pet 销毁）
    aw_sensor.start(parent=pet)
    at_sensor.start(parent=pet)
    clip_sensor.start(parent=pet)

    # === IM 消息接入（docs/PRD-im-message-notify.md M1：QQ / OneBot 11）===
    # IMManager 后台线程收消息，Qt signal 队列投递回主线程弹通知；
    # 通知通道（气泡/托盘）取自 im.notify 配置，免打扰时段由 filter 兜底。
    from core.im.manager import IMManager
    im_manager = IMManager(parent=pet)
    _im_state_text = {"connecting": "连接中", "connected": "已连接",
                      "disconnected": "连接断开（自动重连中）", "error": "连接错误"}

    def _on_im_message(msg) -> None:
        try:
            notify_cfg = (im_manager.config.get("notify") or {})
            if notify_cfg.get("tray", True):
                pet.tray.showMessage("Amadeus 消息", msg.display())
            # 通话中不弹气泡（分层气泡会盖在 CallView 上干扰通话界面）
            if getattr(pet, "_in_call", False):
                return
            if notify_cfg.get("bubble", True):
                pet._show_layered_bubbles(msg.display())
        except Exception:
            pass  # IM 通知永不影响主流程

    def _on_im_status(state: str, detail: str) -> None:
        try:
            pet.tray.showMessage("IM 接入", _im_state_text.get(state, state))
        except Exception:
            pass

    im_manager.message_notified.connect(_on_im_message)
    im_manager.status_changed.connect(_on_im_status)
    im_manager.start()


    # 用户发消息时更新 companion 冷却时间戳（包装原 _send）
    _original_pet_send = pet._send

    def _send_with_companion_cooldown(*args, **kwargs):
        companion_ctrl.on_user_message()
        return _original_pet_send(*args, **kwargs)

    pet._send = _send_with_companion_cooldown
    if os.environ.get("AMADEUS_UI_SNAPSHOT"):
        def save_snapshot() -> None:
            if os.environ.get("AMADEUS_UI_SNAPSHOT") == "bubble":
                pet._set_bubble_text("我已经理解你的任务。接下来会先检查当前桌面状态，\n再执行必要操作，并把最终结果告诉你。")
                pet.reply_bubble.show()
            pet.grab().save(str(ROOT / "data" / "ui-snapshot.png"), "PNG")
            app.quit()
        QTimer.singleShot(7000, save_snapshot)
    return app.exec()


def main() -> int:
    mp.freeze_support()
    if not acquire_single_instance("Amadeus2026.DesktopPet"):
        return 0
    _write_runtime_log(
        "desktop-pet-entry.log",
        f"executable={sys.executable}\nargv={sys.argv!r}\n"
        f"frozen={getattr(sys, 'frozen', False)}\n",
    )

    def start_voice_service() -> None:
        try:
            maybe_start_gpt_sovits()
        except Exception:
            _write_runtime_log("tts-autostart-crash.log", traceback.format_exc())

    # 语音服务探测/模型启动可能访问网络或加载数十秒，不能阻塞桌宠首屏。
    threading.Thread(target=start_voice_service, daemon=True).start()
    try:
        READY_FILE.unlink(missing_ok=True)
        parent_connection, child_connection = mp.Pipe(duplex=True)
        renderer = mp.Process(target=renderer_process, args=(child_connection,), daemon=True)
        renderer.start()
        _write_runtime_log(
            "desktop-pet-startup.log",
            f"executable={sys.executable}\nargv={sys.argv!r}\n"
            f"frozen={getattr(sys, 'frozen', False)}\nrenderer_pid={renderer.pid}\n",
        )
    except Exception:
        _write_runtime_log("desktop-pet-crash.log", traceback.format_exc())
        raise
    try:
        return run_overlay(parent_connection, renderer)
    finally:
        if renderer.is_alive():
            renderer.terminate()
        renderer.join(timeout=2)
        # 清理 GPT-SoVITS 本地子进程 + SSH 隧道（避免孤儿进程占显存/端口）
        try:
            stop_gpt_sovits()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
