"""Amadeus 登录窗口（对应原 amadeus/src/components/LoginForm.tsx）。

特性：
- 全屏 bgLogin.jpg 背景
- 顶部 SpritePlayer logo（sprite_logo.png 6 行 × 7 列 / 38 帧 / 20fps）
- 中下 USER ID / PASSWORD 输入框 + 登录按钮（login_button.png）
- 背景 BGM：login.mp3 循环
- 记住账号密码（持久化到 ~/.amadeus/saved_logins.json）
- 已保存账号快速选择按钮（≥2 个才显示）
- 整体随窗口高度同步缩放（基准 900px）

登录成功后发出 login_success 信号，由 main.py 接管切换到聊天窗口（后续步骤实现）。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QSize, QUrl
from PySide6.QtGui import QPixmap, QFont, QKeyEvent, QPainter, QIcon
try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    _HAS_MULTIMEDIA = True
except ImportError:
    _HAS_MULTIMEDIA = False
    QMediaPlayer = None  # type: ignore[assignment]
    QAudioOutput = None  # type: ignore[assignment]
from PySide6.QtWidgets import (
    QWidget, QLabel, QLineEdit, QPushButton, QCheckBox,
    QVBoxLayout, QHBoxLayout, QFrame, QSizePolicy,
)

from config import DEFAULT_CHARACTER, resources_path, find_character_by_login
from core.auth import set_session_logged_in, set_session_character_id
from core.storage import load_saved_logins, save_logins
from ui.widgets.sprite_player import SpritePlayer


# === 缩放基准（移植自 LoginForm.tsx） ===
BASE_HEIGHT = 900
BASE_FONT = 16


class LoginWindow(QWidget):
    """登录窗口（无父窗口，独立顶层）。"""

    login_success = Signal(str)  # 登录成功时发出，参数为 character_id

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Amadeus · Login")
        self.setWindowState(Qt.WindowMaximized)  # 全屏启动
        self._scale = 1.0

        # === 背景 ===
        bg_path = str(resources_path(DEFAULT_CHARACTER.bg_login_image))
        self._bg_pixmap = QPixmap(bg_path)
        if self._bg_pixmap.isNull():
            print(f"[LoginWindow] WARNING: 背景图加载失败 {bg_path}")

        # === BGM（可选：QtMultimedia 缺失时跳过） ===
        self._bgm = None
        if _HAS_MULTIMEDIA:
            try:
                self._bgm = QMediaPlayer(self)
                self._audio_out = QAudioOutput(self)
                self._bgm.setAudioOutput(self._audio_out)
                self._bgm.setSource(QUrl.fromLocalFile(str(resources_path(DEFAULT_CHARACTER.bgm))))
                self._bgm.setLoops(QMediaPlayer.Infinite)
                self._audio_out.setVolume(0.6)
                self._bgm.play()
            except Exception as e:
                print(f"[LoginWindow] BGM 播放失败（已忽略）: {e}")
                self._bgm = None

        # === 保存的账号 ===
        saved = load_saved_logins()
        self._saved_accounts: dict[str, str] = dict(saved.get("accounts", {}))
        self._last_account: str = saved.get("lastAccount", "")

        # === UI ===
        self._build_ui()
        self._apply_scale()

        # 自动填充最近登录的账号
        if self._last_account and self._last_account in self._saved_accounts:
            self._user_edit.setText(self._last_account)
            self._pwd_edit.setText(self._saved_accounts[self._last_account])
            self._remember_check.setChecked(True)

    # ========== UI 构建 ==========
    def _build_ui(self) -> None:
        # 顶部 Logo
        self._logo = SpritePlayer(
            source=str(resources_path(DEFAULT_CHARACTER.sprite_logo)),
            rows=6,
            columns=7,
            fps=20,
            total_frames=38,
            loop=1,  # 播放一次定格在最后一帧（与原项目一致）
            display_size=QSize(520, 520),
            parent=self,
        )

        # 输入框组
        self._user_label = QLabel("USER ID")
        self._pwd_label = QLabel("PASSWORD")
        self._user_edit = QLineEdit()
        self._pwd_edit = QLineEdit()
        self._pwd_edit.setEchoMode(QLineEdit.Password)

        for w in (self._user_label, self._pwd_label):
            w.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self._user_edit.setFixedWidth(420)
        self._pwd_edit.setFixedWidth(420)

        self._remember_check = QCheckBox("记住账号密码")

        # 登录按钮（图片按钮；图加载失败时显示文字 LOGIN 作后备）
        self._login_btn = QPushButton("LOGIN")
        btn_path = str(resources_path("/login_button.png"))
        btn_pixmap = QPixmap(btn_path)
        if not btn_pixmap.isNull():
            self._login_btn.setIcon(QIcon(btn_pixmap))
            self._login_btn.setText("")
        else:
            print(f"[LoginWindow] WARNING: 登录按钮图片加载失败 {btn_path}，使用文字按钮")
        # 默认 80×80，缩放系数会在 _apply_scale 中重设
        self._login_btn.setFixedSize(80, 80)
        self._login_btn.setIconSize(QSize(80, 80))
        self._login_btn.setCursor(Qt.PointingHandCursor)
        self._login_btn.setFlat(True)
        self._login_btn.setStyleSheet(
            "QPushButton { border: 1px solid #D18B24; background: rgba(0,0,0,0.6); "
            "color: #F2B03A; font-family: 'Consolas'; font-weight: bold; }"
            "QPushButton:hover { background: #D18B24; }"
        )
        self._login_btn.clicked.connect(self._handle_login)

        # 快速账号选择容器
        self._quick_accounts_layout = QHBoxLayout()
        self._quick_accounts_layout.setSpacing(8)
        self._quick_accounts_container = QWidget()
        self._quick_accounts_container.setLayout(self._quick_accounts_layout)
        self._quick_accounts_container.setVisible(False)

        # 错误信息
        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: red; background: transparent;")
        self._error_label.setAlignment(Qt.AlignCenter)
        self._error_label.setVisible(False)

        # 输入组（标签 + 输入框 两行 + 记住密码 + 快速账号 + 错误信息）
        right_inputs = QVBoxLayout()
        right_inputs.setSpacing(12)
        right_inputs.addWidget(self._build_row(self._user_label, self._user_edit))
        right_inputs.addWidget(self._build_row(self._pwd_label, self._pwd_edit))
        right_inputs.addWidget(self._remember_check, alignment=Qt.AlignRight)
        right_inputs.addWidget(self._quick_accounts_container, alignment=Qt.AlignRight)
        right_inputs.addWidget(self._error_label)

        # 中心水平布局：输入组 + 登录按钮
        center_h = QHBoxLayout()
        center_h.setSpacing(24)
        center_h.addStretch(1)
        center_h.addLayout(right_inputs)
        center_h.addWidget(self._login_btn, alignment=Qt.AlignBottom)
        center_h.addStretch(1)

        # 整体垂直布局
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 顶部空白（Logo 占位）
        outer.addSpacing(120)
        outer.addWidget(self._logo, alignment=Qt.AlignHCenter)
        outer.addStretch(4)
        outer.addLayout(center_h)
        outer.addStretch(1)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._refresh_quick_accounts()

    def _build_row(self, label: QLabel, edit: QLineEdit) -> QWidget:
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(label)
        row.addWidget(edit)
        row.addStretch(1)
        container = QWidget()
        container.setLayout(row)
        return container

    # ========== 缩放 ==========
    def _apply_scale(self) -> None:
        """根据当前窗口高度同步缩放字体和控件（对应原项目 BASE_HEIGHT 基准）。"""
        h = max(self.height(), 1)
        self._scale = h / BASE_HEIGHT

        # 字体（QSS 中用 pt，这里通过 QFont.setPointSize）
        font_user = QFont("Consolas", int(18 * self._scale))
        font_label = QFont("Cinzel", int(18 * self._scale))
        font_label.setWeight(QFont.Medium)
        self._user_edit.setFont(font_user)
        self._pwd_edit.setFont(font_user)
        self._user_label.setFont(font_label)
        self._pwd_label.setFont(font_label)

        # 标签 + 输入框样式（移植自 LoginForm.tsx 的 inputStyle / labelStyle）
        accent = "#F2B03A"
        border = "#D18B24"
        self.setStyleSheet(f"""
            QLabel {{
                color: {accent};
                letter-spacing: 3px;
                background: transparent;
            }}
            QLineEdit {{
                background: #000000;
                border: 2px solid {border};
                color: {accent};
                padding: 0 16px;
                font-family: 'Consolas', 'Courier New', monospace;
                letter-spacing: 2px;
                selection-background-color: {border};
            }}
            QLineEdit:focus {{
                border: 2px solid {accent};
            }}
            QCheckBox {{
                color: {accent};
                background: transparent;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: {int(18 * self._scale)}px;
                height: {int(18 * self._scale)}px;
            }}
        """)

        # 输入框宽度随高度等比
        new_w = int(420 * self._scale)
        self._user_edit.setFixedWidth(new_w)
        self._pwd_edit.setFixedWidth(new_w)

        # Logo 大小
        logo_size = int(520 * self._scale)
        self._logo.setFixedSize(logo_size, logo_size)

        # 登录按钮大小
        btn_size = int(80 * self._scale)
        self._login_btn.setFixedSize(btn_size, btn_size)
        self._login_btn.setIconSize(QSize(btn_size, btn_size))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_scale()

    def paintEvent(self, event) -> None:  # noqa: N802
        # 绘制背景图（覆盖整个窗口）
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if not self._bg_pixmap.isNull():
            painter.drawPixmap(self.rect(), self._bg_pixmap, self._bg_pixmap.rect())
        painter.end()

    # ========== 登录逻辑 ==========
    def _refresh_quick_accounts(self) -> None:
        """清空并重新生成已保存账号的快速选择按钮（≥2 个才显示）。"""
        # 清空旧按钮
        while self._quick_accounts_layout.count():
            item = self._quick_accounts_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if len(self._saved_accounts) < 2:
            self._quick_accounts_container.setVisible(False)
            return

        self._quick_accounts_container.setVisible(True)
        for acct in self._saved_accounts.keys():
            btn = QPushButton(acct)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: rgba(0,0,0,0.6);
                    border: 1px solid #D18B24;
                    color: #F2B03A;
                    padding: 4px 12px;
                    font-family: 'Consolas', monospace;
                }
                QPushButton:hover {
                    background: #D18B24;
                }
            """)
            btn.clicked.connect(self._make_quick_handler(acct))
            self._quick_accounts_layout.addWidget(btn)

    def _make_quick_handler(self, account: str):
        def handler():
            self._user_edit.setText(account)
            self._pwd_edit.setText(self._saved_accounts.get(account, ""))
            self._remember_check.setChecked(True)
        return handler

    def _show_error(self, msg: str) -> None:
        self._error_label.setText(msg)
        self._error_label.setVisible(True)

    def _handle_login(self) -> None:
        try:
            username = self._user_edit.text().strip()
            password = self._pwd_edit.text().strip()
            print(f"[LoginWindow] 尝试登录 user={username!r} pwd_len={len(password)}")

            if not username:
                self._show_error("请输入 USER ID")
                return
            if not password:
                self._show_error("请输入 PASSWORD")
                return

            character = find_character_by_login(username, password)
            if character is None:
                self._show_error("USER ID 或 PASSWORD 错误")
                return

            print(f"[LoginWindow] 登录角色 {character.id} ({character.name})")

            # 校验通过：设置内存会话登录态
            set_session_logged_in(True)
            set_session_character_id(character.id)

            # 记住账号密码
            if self._remember_check.isChecked():
                self._saved_accounts[username] = password
            else:
                self._saved_accounts.pop(username, None)
            save_logins(self._saved_accounts, username)
            self._refresh_quick_accounts()

            # 停止 BGM 后发出登录成功信号
            if self._bgm is not None:
                self._bgm.stop()
            self.login_success.emit(character.id)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._show_error(f"登录异常：{e}")

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._handle_login()
        else:
            super().keyPressEvent(event)
