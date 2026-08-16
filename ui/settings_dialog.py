"""Application settings with model, voice, input and about tabs."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QTabWidget,
    QVBoxLayout, QWidget,
)

from core.storage import load_config, save_config

CRT_QSS = """
QDialog#settingsDialog { background-color: #171114; color: #c1b492; border: 1px solid #d2738a; }
QWidget#settingsTitleBar { background-color: #21171b; border: 1px solid #d2738a; border-left: 8px solid #d2738a; }
QLabel#settingsTitle { color: #c1b492; font: 700 12px "Consolas", "SimSun"; letter-spacing: 0px; }
QLabel#settingsSignature { color: #8a7f63; font: 10px "Consolas", "SimSun"; }
QPushButton#settingsClose { background: #171114; color: #d2738a; border: 1px solid #d2738a; min-width: 24px; max-width: 24px; min-height: 22px; max-height: 22px; padding: 0; font: 700 14px "Consolas"; }
QPushButton#settingsClose:hover { background: #d2738a; color: #171114; }
QTabWidget::pane { border: 1px solid #d2738a; background: #171114; top: -1px; }
QTabBar::tab { background: #21171b; color: #8a7f63; border: 1px solid #8a7f63; border-bottom: 0; padding: 7px 12px; min-width: 72px; font: 12px "Times New Roman", "SimSun"; }
QTabBar::tab:selected { background: #d2738a; color: #171114; border-color: #d2738a; }
QTabBar::tab:hover:!selected { color: #c1b492; border-color: #d2738a; }
QWidget#settingsPage { background-color: #171114; }
QScrollArea { background: transparent; border: 0; }
QScrollArea > QWidget > QWidget { background-color: #171114; }
QLabel { color: #c1b492; font: 13px "Times New Roman", "SimSun"; }
QLabel#sectionHeader { color: #d2738a; border-left: 3px solid #d2738a; padding: 3px 0 3px 8px; font: 700 12px "Times New Roman", "SimSun"; }
QLineEdit, QComboBox { background-color: #21171b; color: #c1b492; border: 1px solid #8a7f63; border-radius: 0; padding: 7px 9px; min-height: 22px; selection-background-color: #d2738a; selection-color: #171114; font: 13px "Consolas", "SimSun"; }
QLineEdit:focus, QComboBox:focus { border-color: #d2738a; background-color: #171114; }
QComboBox::drop-down { border-left: 1px solid #8a7f63; width: 24px; }
QComboBox QAbstractItemView { background-color: #171114; color: #c1b492; border: 1px solid #d2738a; selection-background-color: #d2738a; selection-color: #171114; }
QCheckBox { color: #c1b492; spacing: 8px; font: 13px "Times New Roman", "SimSun"; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #8a7f63; background: #21171b; }
QCheckBox::indicator:checked { background: #d2738a; border-color: #d2738a; }
QPushButton { background-color: #21171b; color: #c1b492; border: 1px solid #c1b492; border-radius: 0; padding: 7px 13px; min-height: 24px; font: 700 12px "Times New Roman", "SimSun"; }
QPushButton:hover { background-color: #d2738a; color: #171114; border-color: #d2738a; }
QPushButton:pressed { background-color: #c1b492; color: #171114; }
QDialogButtonBox QPushButton { min-width: 88px; }
QScrollBar:vertical { background: #21171b; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #d2738a; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


def _section(title: str) -> QLabel:
    label = QLabel(title)
    label.setObjectName("sectionHeader")
    return label


def _tune_form(form: QFormLayout) -> None:
    form.setContentsMargins(18, 18, 18, 18)
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(11)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setLabelAlignment(Qt.AlignRight)


def _scroll_page(page: QWidget) -> QScrollArea:
    page.setObjectName("settingsPage")
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setWidget(page)
    return scroll


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Amadeus 设置")
        self.setMinimumSize(680, 500)
        self.resize(760, 560)
        self.setStyleSheet(CRT_QSS)
        config = load_config()
        tabs = QTabWidget()

        model_page = QWidget()
        model_form = QFormLayout(model_page)
        _tune_form(model_form)
        model_form.addRow(_section("NETWORK / MODEL"))
        model_form.addRow(QLabel("默认后端：直连 OpenAI 兼容 API。"))
        self.endpoint = QLineEdit(config.get("endpoint", "https://api.deepseek.com/v1"))
        self.api_key = QLineEdit(config.get("api_key", ""))
        self.api_key.setEchoMode(QLineEdit.Password)
        self.model = QLineEdit(config.get("model", "deepseek-chat"))
        model_form.addRow("API Endpoint", self.endpoint)
        model_form.addRow("API Key", self.api_key)
        model_form.addRow("模型", self.model)
        tabs.addTab(_scroll_page(model_page), "直连模型（默认）")

        voice_page = QWidget()
        voice_form = QFormLayout(voice_page)
        _tune_form(voice_form)
        voice_form.addRow(_section("VOICE OUTPUT"))
        self.tts_enabled = QCheckBox("回复后自动朗读日语")
        self.tts_enabled.setChecked(config.get("tts_enabled", True))
        self.tts_rate = QComboBox()
        self.tts_rate.addItems(["慢", "正常", "快"])
        self.tts_rate.setCurrentIndex(config.get("tts_rate", 1))
        voice_form.addRow(self.tts_enabled)
        voice_form.addRow("语速", self.tts_rate)
        tabs.addTab(_scroll_page(voice_page), "语音合成")

        asr_page = QWidget()
        asr_form = QFormLayout(asr_page)
        _tune_form(asr_form)
        asr_form.addRow(_section("VOICE INPUT"))
        self.asr_endpoint = QLineEdit(config.get("asr_endpoint", "https://api.xiaomimimo.com/v1"))
        self.asr_key = QLineEdit(config.get("asr_api_key", ""))
        self.asr_key.setEchoMode(QLineEdit.Password)
        self.asr_model = QLineEdit(config.get("asr_model", "mimo-v2.5-asr"))
        asr_form.addRow("ASR Endpoint", self.asr_endpoint)
        asr_form.addRow("ASR API Key", self.asr_key)
        asr_form.addRow("ASR 模型", self.asr_model)
        tabs.addTab(_scroll_page(asr_page), "语音输入")

        # === Agent 模式（2026-08-15 agent-mode spec §4.4）===
        from config import AGENT_ROUTER_DEFAULTS, HERMES_DEFAULTS
        agent_page = QWidget()
        agent_form = QFormLayout(agent_page)
        _tune_form(agent_form)
        agent_form.addRow(_section("AGENT ROUTER"))
        router_cfg = {**AGENT_ROUTER_DEFAULTS, **(config.get("agent_router") or {})}
        self.agent_mode = QComboBox()
        self.agent_mode.addItem("本地直连（默认）", "chat")
        self.agent_mode.addItem("Hermes 网关（deepseek 模式）", "hermes")
        self.agent_mode.addItem("codex 子进程", "codex")
        self.agent_mode.addItem("自动分流（gate）", "auto")
        idx = self.agent_mode.findData(str(router_cfg.get("mode", "chat")))
        self.agent_mode.setCurrentIndex(max(idx, 0))
        agent_form.addRow("Agent 模式", self.agent_mode)

        hermes_cfg = {**HERMES_DEFAULTS, **(config.get("hermes") or {})}
        self.hermes_key = QLineEdit(str(hermes_cfg.get("api_key", "")))
        self.hermes_key.setEchoMode(QLineEdit.Password)
        agent_form.addRow("Hermes API Key", self.hermes_key)

        self.hermes_status = QLabel("未检测")
        self.hermes_status.setStyleSheet("color:#8a7f63")
        hermes_btn = QPushButton("检测 Hermes 网关")
        hermes_btn.clicked.connect(self._probe_hermes)
        agent_form.addRow(self.hermes_status, hermes_btn)

        codex_cfg = {**AGENT_ROUTER_DEFAULTS["codex"], **(router_cfg.get("codex") or {})}
        self.codex_sandbox = QComboBox()
        self.codex_sandbox.addItem("只读（默认）", "read-only")
        self.codex_sandbox.addItem("可写工作区", "workspace-write")
        idx = self.codex_sandbox.findData(str(codex_cfg.get("sandbox", "read-only")))
        self.codex_sandbox.setCurrentIndex(max(idx, 0))
        agent_form.addRow("codex 沙箱", self.codex_sandbox)
        tabs.addTab(_scroll_page(agent_page), "Agent 模式")

        # === Companion 主动问候（2026-08-16 spec §8）===
        from config import COMPANION_DEFAULTS
        companion_page = QWidget()
        companion_form = QFormLayout(companion_page)
        _tune_form(companion_form)
        companion_form.addRow(_section("COMPANION SIGNALS"))
        companion_cfg = {**COMPANION_DEFAULTS, **(config.get("companion") or {})}
        self.companion_enabled = QCheckBox("启用主动陪伴（伪春菜式）")
        self.companion_enabled.setChecked(bool(companion_cfg.get("enabled", True)))
        companion_form.addRow(self.companion_enabled)

        # 传感器逐项开关
        sensors_cfg = {**COMPANION_DEFAULTS["sensors"], **(companion_cfg.get("sensors") or {})}
        self.sensor_active_window = QCheckBox("前台窗口检测（2s）")
        self.sensor_active_window.setChecked(bool(sensors_cfg.get("active_window", True)))
        companion_form.addRow(self.sensor_active_window)
        self.sensor_activity = QCheckBox("工作节奏检测（30s）")
        self.sensor_activity.setChecked(bool(sensors_cfg.get("activity", True)))
        companion_form.addRow(self.sensor_activity)
        self.sensor_idle = QCheckBox("空闲状态检测（派生）")
        self.sensor_idle.setChecked(bool(sensors_cfg.get("idle", True)))
        companion_form.addRow(self.sensor_idle)
        self.sensor_clipboard = QCheckBox("剪贴板检测（默认关，中隐私）")
        self.sensor_clipboard.setChecked(bool(sensors_cfg.get("clipboard", False)))
        companion_form.addRow(self.sensor_clipboard)
        self.sensor_screen = QCheckBox("屏幕感知（默认关，高隐私，成本高）")
        self.sensor_screen.setChecked(bool(sensors_cfg.get("screen", False)))
        companion_form.addRow(self.sensor_screen)

        # 静音时段
        qh = companion_cfg.get("quiet_hours", {"start": "23:00", "end": "08:00"})
        self.quiet_start = QLineEdit(str(qh.get("start", "23:00")))
        self.quiet_end = QLineEdit(str(qh.get("end", "08:00")))
        companion_form.addRow("静音开始", self.quiet_start)
        companion_form.addRow("静音结束", self.quiet_end)

        # 频率
        self.companion_freq = QComboBox()
        self.companion_freq.addItem("低（20%）", "low")
        self.companion_freq.addItem("中（50%）", "mid")
        self.companion_freq.addItem("高（100%）", "high")
        idx = self.companion_freq.findData(str(companion_cfg.get("frequency", "mid")))
        self.companion_freq.setCurrentIndex(max(idx, 0))
        companion_form.addRow("触发频率", self.companion_freq)

        # 每日上限
        self.companion_daily_limit = QLineEdit(str(companion_cfg.get("daily_limit", 30)))
        companion_form.addRow("每日上限", self.companion_daily_limit)

        # 当前上下文预览（只读）
        self.companion_preview = QLabel("（启动后显示）")
        self.companion_preview.setStyleSheet("color:#8a7f63; font-family: monospace;")
        self.companion_preview.setWordWrap(True)
        companion_form.addRow("当前上下文", self.companion_preview)

        # 测试问候 + 清空记忆按钮
        from PySide6.QtWidgets import QHBoxLayout
        btn_row = QHBoxLayout()
        test_btn = QPushButton("测试问候")
        test_btn.clicked.connect(self._test_companion)
        btn_row.addWidget(test_btn)
        clear_btn = QPushButton("清空记忆")
        clear_btn.clicked.connect(self._clear_companion_memory)
        btn_row.addWidget(clear_btn)
        companion_form.addRow(btn_row)

        tabs.addTab(_scroll_page(companion_page), "Companion")

        # === 关于 / 版本 ===
        from core.version import __version__
        about_page = QWidget()
        about_form = QFormLayout(about_page)
        _tune_form(about_form)
        about_form.addRow(_section("BUILD / UPDATE"))
        about_form.addRow("当前版本", QLabel(__version__))
        self.version_check_url = QLineEdit(config.get("version_check_url", ""))
        self.version_check_url.setPlaceholderText("远程版本检查 URL（纯文本，可留空）")
        about_form.addRow("版本检查 URL", self.version_check_url)
        self.version_status = QLabel("未检查")
        self.version_status.setStyleSheet("color:#8a7f63")
        about_form.addRow("最新版本", self.version_status)
        check_btn = QPushButton("检查更新")
        check_btn.clicked.connect(self._check_update)
        about_form.addRow(check_btn)
        tabs.addTab(_scroll_page(about_page), "关于")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        title_bar = QWidget(self)
        title_bar.setObjectName("settingsTitleBar")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 4, 6, 4)
        title_layout.setSpacing(8)
        title = QLabel("AMADEUS CONFIG", title_bar)
        title.setObjectName("settingsTitle")
        signature = QLabel("tait-crt-interface-skill", title_bar)
        signature.setObjectName("settingsSignature")
        close_btn = QPushButton("X", title_bar)
        close_btn.setObjectName("settingsClose")
        close_btn.setToolTip("??")
        close_btn.clicked.connect(self.reject)
        title_layout.addWidget(title)
        title_layout.addWidget(signature)
        title_layout.addStretch()
        title_layout.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        layout.addWidget(title_bar)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and event.position().y() <= 42:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton and not self._drag_pos.isNull():
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = QPoint()
        super().mouseReleaseEvent(event)

    def _check_update(self) -> None:
        from core.version import __version__, check_latest_version, parse_version
        url = self.version_check_url.text().strip()
        self.version_status.setText("检查中…")
        QApplication.processEvents()
        latest = check_latest_version(url)
        if latest is None:
            self.version_status.setText("未配置 URL 或检查失败")
            self.version_status.setStyleSheet("color:#8a7f63")
            return
        try:
            if parse_version(latest) > parse_version(__version__):
                self.version_status.setText(f"{latest}（有新版）")
                self.version_status.setStyleSheet("color:#d2738a")
            else:
                self.version_status.setText(f"{latest}（已是最新）")
                self.version_status.setStyleSheet("color:#34c759")
        except ValueError:
            self.version_status.setText(f"{latest}（版本号格式异常）")
            self.version_status.setStyleSheet("color:#8a7f63")

    def _probe_hermes(self) -> None:
        """同步探测 Hermes 网关 /health（2s 超时，设置页内可接受）。"""
        from config import HERMES_DEFAULTS
        from core.hermes_launcher import probe_health, read_profile_api_key
        hermes_cfg = {**HERMES_DEFAULTS, **(load_config().get("hermes") or {})}
        base_url = str(hermes_cfg.get("base_url") or "http://127.0.0.1:8642")
        api_key = str(hermes_cfg.get("api_key") or "") or (read_profile_api_key() or "")
        self.hermes_status.setText("检测中…")
        QApplication.processEvents()
        ok = probe_health(base_url, api_key)
        self.hermes_status.setText("在线" if ok else "离线")
        self.hermes_status.setStyleSheet("color:#34c759" if ok else "color:#d2738a")

    def _test_companion(self) -> None:
        """手动触发一次 companion 问候（用于设置页验收）。"""
        from core.companion.evaluator import Evaluator
        from core.companion.sensors import ContextSnapshot
        from datetime import datetime
        now = datetime.now()
        local_time = now.strftime("%H:%M 周%w")
        is_deep_night = 23 <= now.hour or now.hour < 6
        snap = ContextSnapshot(
            timestamp=now.isoformat(), local_time=local_time,
            is_deep_night=is_deep_night, idle_seconds=10,
            work_session_minutes=5, idle_state="active",
            active_window_title="（测试）", active_process="test.exe",
            window_changed_recently=False,
            last_companion_greeting_ts=None,
            last_companion_topic=None, greeting_count_today=0,
        )
        ev = Evaluator()
        # 强制走 LLM 路径（即便 L1 不命中）
        cfg = load_config()
        decision = ev.evaluate(
            snap, allow_llm=True, signal_type="test",
            llm_endpoint=cfg.get("endpoint", ""),
            llm_api_key=cfg.get("api_key", ""),
            llm_model=cfg.get("model", ""),
        )
        if decision:
            self.companion_preview.setText(
                f"[{decision.source}] {decision.emotion}: {decision.text}"
            )
        else:
            self.companion_preview.setText("（LLM 判断不说话）")

    def _clear_companion_memory(self) -> None:
        """清空 lightweight_memory 表。"""
        from core.companion.storage import clear_all, init_schema
        init_schema()
        clear_all()
        self.companion_preview.setText("已清空记忆")

    def _save(self) -> None:
        config = load_config()
        config.update({
            "endpoint": self.endpoint.text().strip(), "api_key": self.api_key.text().strip(),
            "model": self.model.text().strip(), "tts_enabled": self.tts_enabled.isChecked(),
            "tts_rate": self.tts_rate.currentIndex(), "asr_endpoint": self.asr_endpoint.text().strip(),
            "asr_api_key": self.asr_key.text().strip(), "asr_model": self.asr_model.text().strip(),
            "version_check_url": self.version_check_url.text().strip(),
        })
        from config import AGENT_ROUTER_DEFAULTS, HERMES_DEFAULTS
        router_cfg = {**AGENT_ROUTER_DEFAULTS, **(config.get("agent_router") or {})}
        codex_cfg = {**AGENT_ROUTER_DEFAULTS["codex"], **(router_cfg.get("codex") or {})}
        codex_cfg["sandbox"] = self.codex_sandbox.currentData()
        config["agent_router"] = {"mode": self.agent_mode.currentData(), "codex": codex_cfg}
        hermes_cfg = {**HERMES_DEFAULTS, **(config.get("hermes") or {})}
        hermes_cfg["api_key"] = self.hermes_key.text().strip()
        config["hermes"] = hermes_cfg
        # companion 配置
        from config import COMPANION_DEFAULTS
        companion_cfg = {**COMPANION_DEFAULTS, **(config.get("companion") or {})}
        companion_cfg["enabled"] = self.companion_enabled.isChecked()
        companion_cfg["sensors"] = {
            "active_window": self.sensor_active_window.isChecked(),
            "activity": self.sensor_activity.isChecked(),
            "idle": self.sensor_idle.isChecked(),
            "clipboard": self.sensor_clipboard.isChecked(),
            "screen": self.sensor_screen.isChecked(),
        }
        companion_cfg["quiet_hours"] = {
            "start": self.quiet_start.text().strip(),
            "end": self.quiet_end.text().strip(),
        }
        companion_cfg["frequency"] = self.companion_freq.currentData()
        try:
            companion_cfg["daily_limit"] = int(self.companion_daily_limit.text().strip())
        except ValueError:
            companion_cfg["daily_limit"] = 30
        config["companion"] = companion_cfg
        save_config(config)
        self.accept()
