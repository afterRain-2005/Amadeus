"""Application settings with model, voice, input and about tabs."""
from __future__ import annotations

import threading

from PySide6.QtCore import Q_ARG, QMetaObject, QPoint, Qt, QTimer, Slot
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QTabWidget,
    QVBoxLayout, QWidget,
)

from core.storage import load_config, save_config
from ui.widgets.crt_title_bar import CrtTitleBar

from pathlib import Path
_DITHER_URL = str(
    Path(__file__).resolve().parent.parent / "resources" / "textures" / "dither_rose.png"
).replace("\\", "/")
_ABOUT_BG_URL = str(
    Path(__file__).resolve().parent.parent / "resources" / "ui" / "helivitica-bg.png"
).replace("\\", "/")

# Poiret One 字体（懒加载，在 QApplication 就绪后调用）
_POIRET_FONT = str(
    Path(__file__).resolve().parent.parent / "resources" / "fonts" / "PoiretOne-Regular.ttf"
).replace("\\", "/")
_POIRET_FONT_LOADED = False

def _ensure_poiret_font() -> None:
    global _POIRET_FONT_LOADED
    if not _POIRET_FONT_LOADED:
        QFontDatabase.addApplicationFont(_POIRET_FONT)
        _POIRET_FONT_LOADED = True

_QSS_TMPL = """
QDialog#settingsDialog { background-color: #171114; color: #c1b492; border: 1px solid #d2738a; background-image: url(__DITHER__); }
QTabWidget::pane { border: 1px solid #d2738a; background-color: #08031a; background-image: url(ABOUT_BG); background-repeat: no-repeat; background-position: left top; top: -1px; }
QTabBar::tab { background: #21171b; color: #8a7f63; border: 1px solid #8a7f63; border-bottom: 0; padding: 7px 12px; min-width: 72px; font: 12px "Consolas", "Microsoft YaHei"; }
QTabBar::tab:selected { background: #d2738a; color: #171114; border-color: #d2738a; }
QTabBar::tab:hover:!selected { color: #c1b492; border-color: #d2738a; }
QWidget#settingsPage { background-color: transparent; }
QScrollArea { background: transparent; border: 0; }
QScrollArea > QWidget > QWidget { background-color: transparent; }
QLabel { color: #c1b492; font: 13px "Consolas", "Microsoft YaHei"; }
QLabel#sectionHeader { color: #d2738a; border-left: 3px solid #d2738a; padding: 3px 0 3px 8px; font: 700 12px "Consolas", "Microsoft YaHei"; }
QLineEdit, QComboBox { background-color: rgba(23, 17, 20, 178); color: #c1b492; border: 1px solid #8a7f63; border-radius: 0; padding: 7px 9px; min-height: 22px; selection-background-color: #d2738a; selection-color: #171114; font: 13px "Consolas", "Microsoft YaHei"; }
QLineEdit:focus, QComboBox:focus { border-color: #d2738a; background-color: rgba(23, 17, 20, 204); }
QComboBox::drop-down { border-left: 1px solid #8a7f63; width: 24px; }
QComboBox QAbstractItemView { background-color: rgba(23, 17, 20, 222); color: #c1b492; border: 1px solid #d2738a; selection-background-color: #d2738a; selection-color: #171114; }
QCheckBox { color: #c1b492; spacing: 8px; font: 13px "Consolas", "Microsoft YaHei"; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #8a7f63; background: #21171b; }
QCheckBox::indicator:checked { background: #d2738a; border-color: #d2738a; }
QPushButton { background-color: #21171b; color: #c1b492; border: 1px solid #c1b492; border-radius: 0; padding: 7px 13px; min-height: 24px; font: 700 12px "Consolas", "Microsoft YaHei"; }
QPushButton:hover { background-color: #d2738a; color: #171114; border-color: #d2738a; }
QPushButton:pressed { background-color: #c1b492; color: #171114; }
QDialogButtonBox QPushButton { min-width: 88px; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #d2738a; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""

CRT_QSS = _QSS_TMPL.replace("__DITHER__", _DITHER_URL).replace("ABOUT_BG", _ABOUT_BG_URL)

# 设置页底部左侧滚动台词（fauux 长句 marquee；El Psy Kongroo）
_TICKER_TEXT = (
    "There is no end though there is a start in space. — Infinity. "
    "It has own power, it ruins, and it goes though there is a start also in the star. — Finite. "
    "Only the person who was wisdom can read the most foolish one from the history. "
    "The fish that lives in the sea doesn't know the world in the land. "
    "It also ruins and goes if they have wisdom. "
    "It is funnier that man exceeds the speed of light than fish start living in the land. "
    "It can be said that this is an final ultimatum from the god to the people who can fight."
)


def _section(title: str) -> QLabel:
    label = QLabel(title)
    label.setObjectName("sectionHeader")
    glow = QGraphicsDropShadowEffect(label)
    glow.setColor(QColor(210, 115, 138, 140))
    glow.setBlurRadius(8)
    glow.setOffset(0, 0)
    label.setGraphicsEffect(glow)
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
        _ensure_poiret_font()
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Amadeus 设置")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self._drag_pos = QPoint()
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
        voice_layout = QVBoxLayout(voice_page)
        voice_layout.setContentsMargins(0, 0, 0, 0)
        voice_layout.setSpacing(0)
        voice_form = QFormLayout()
        _tune_form(voice_form)
        voice_form.addRow(_section("VOICE OUTPUT"))
        from config import TTS_PROVIDER_DEFAULT
        self.tts_provider = QComboBox()
        self.tts_provider.addItem("GPT-SoVITS（本地/SSH）", "gpt_sovits")
        self.tts_provider.addItem("阿里云百炼 Qwen-TTS", "aliyun")
        idx = self.tts_provider.findData(config.get("tts_provider", TTS_PROVIDER_DEFAULT))
        self.tts_provider.setCurrentIndex(max(idx, 0))
        self.tts_enabled = QCheckBox("回复后自动朗读日语")
        self.tts_enabled.setChecked(config.get("tts_enabled", True))
        self.tts_rate = QComboBox()
        self.tts_rate.addItems(["慢", "正常", "快"])
        self.tts_rate.setCurrentIndex(config.get("tts_rate", 1))
        voice_form.addRow("TTS Provider", self.tts_provider)
        voice_form.addRow(self.tts_enabled)
        voice_form.addRow("语速", self.tts_rate)
        voice_layout.addLayout(voice_form)

        asr_page = QWidget()
        asr_form = QFormLayout(asr_page)
        _tune_form(asr_form)
        asr_form.addRow(_section("VOICE INPUT"))
        self.asr_endpoint = QLineEdit(config.get("asr_endpoint", "https://api.xiaomimimo.com/v1"))
        self.asr_key = QLineEdit(config.get("asr_api_key", ""))
        self.asr_key.setEchoMode(QLineEdit.Password)
        self.asr_model = QLineEdit(config.get("asr_model", "mimo-audio-v1"))
        asr_form.addRow("ASR Endpoint", self.asr_endpoint)
        asr_form.addRow("ASR API Key", self.asr_key)
        asr_form.addRow("ASR 模型", self.asr_model)

        # 通话麦克风设备：PortAudio 默认设备常与 Windows 通信默认不一致
        # （实测默认拿到空置的 Realtek 插孔 → 通话无声/波形死平），
        # 允许显式指定（自动扫描只在设备明显更差时兜底）
        self.mic_device = QComboBox()
        self.mic_device.addItem("自动（系统默认 + 死设备扫描）", -1)
        try:
            import sounddevice as _sd
            for _i, _d in enumerate(_sd.query_devices()):
                if _d["max_input_channels"] > 0:
                    self.mic_device.addItem(
                        f"[{_i}] {str(_d['name'])[:40]}", _i)
        except Exception:
            pass
        _mic_idx = config.get("mic_device_index", -1)
        _mi = self.mic_device.findData(_mic_idx if isinstance(_mic_idx, int) else -1)
        self.mic_device.setCurrentIndex(max(_mi, 0))
        asr_form.addRow("通话麦克风", self.mic_device)

        # === 软件回声消除 AEC（可调；core/aec.py）===
        from config import AEC_PARAMS
        aec_cfg = {**AEC_PARAMS, **(config.get("aec") or {})}
        asr_form.addRow(_section("ECHO CANCELLATION (AEC)"))
        self.aec_enabled = QCheckBox("启用软件回声消除（她说话时可正常插话打断；关闭则退回音量门槛打断）")
        self.aec_enabled.setChecked(bool(aec_cfg.get("enabled", True)))
        asr_form.addRow(self.aec_enabled)

        self.aec_filter_len = QComboBox()
        for label, ms in [("80ms（低延迟，小房间）", 80), ("120ms（默认，推荐）", 120),
                          ("200ms（混响大的房间）", 200)]:
            self.aec_filter_len.addItem(label, ms)
        _fl = self.aec_filter_len.findData(int(aec_cfg.get("filter_len_ms", 120)))
        self.aec_filter_len.setCurrentIndex(max(_fl, 0))
        asr_form.addRow("滤波器长度", self.aec_filter_len)

        self.aec_mu = QComboBox()
        for label, mu in [("稳（慢收敛）", 0.2), ("平衡（默认）", 0.5), ("快（易过冲）", 0.8)]:
            self.aec_mu.addItem(label, mu)
        _mu = self.aec_mu.findData(float(aec_cfg.get("mu", 0.5)))
        self.aec_mu.setCurrentIndex(max(_mu, 0))
        asr_form.addRow("收敛速度", self.aec_mu)

        self.aec_align = QComboBox()
        for label, ms in [("40ms", 40), ("80ms（默认）", 80), ("120ms", 120)]:
            self.aec_align.addItem(label, ms)
        _al = self.aec_align.findData(int(aec_cfg.get("align_delay_ms", 80)))
        self.aec_align.setCurrentIndex(max(_al, 0))
        asr_form.addRow("对齐延迟", self.aec_align)

        self.aec_nlp = QComboBox()
        for label, g in [("关闭", 0.0), ("弱", 0.3), ("中（默认）", 0.6), ("强", 0.85)]:
            self.aec_nlp.addItem(label, g)
        _nlp = self.aec_nlp.findData(float(aec_cfg.get("nlp_gain", 0.6)))
        self.aec_nlp.setCurrentIndex(max(_nlp, 0))
        asr_form.addRow("残余回声抑制", self.aec_nlp)

        # === GPT-SoVITS 运行模式（独立容器，选择 gpt_sovits 时显示）===
        from config import GPT_SOVITS_DEFAULTS
        from core.ssh_config_parser import parse_ssh_config
        from core.ssh_tunnel import SSHTunnel
        self._gpt_block = QWidget()
        gpt_form = QFormLayout(self._gpt_block)
        _tune_form(gpt_form)
        gpt_form.addRow(_section("GPT-SOVITS BACKEND"))
        gpt_cfg = {**GPT_SOVITS_DEFAULTS, **(config.get("gpt_sovits") or {})}
        self.gpt_mode = QComboBox()
        self.gpt_mode.addItem("本地启动（本机 GPU）", "local")
        self.gpt_mode.addItem("SSH 隧道（远程 GPU）", "ssh")
        self.gpt_mode.addItem("自动（优先 SSH，回退本地）", "auto")
        idx = self.gpt_mode.findData(str(gpt_cfg.get("mode", "auto")))
        self.gpt_mode.setCurrentIndex(max(idx, 0))
        gpt_form.addRow("运行模式", self.gpt_mode)

        self.gpt_ssh_host = QComboBox()
        self.gpt_ssh_host.setEditable(False)
        ssh_hosts = parse_ssh_config()
        self._ssh_hosts = ssh_hosts
        current_ssh = str(gpt_cfg.get("ssh_host", ""))
        for h in ssh_hosts:
            self.gpt_ssh_host.addItem(h.display(), h.host)
        if current_ssh:
            idx = self.gpt_ssh_host.findData(current_ssh)
            if idx >= 0:
                self.gpt_ssh_host.setCurrentIndex(idx)
        gpt_form.addRow("SSH Host（读自 ~/.ssh/config）", self.gpt_ssh_host)

        if not ssh_hosts:
            ssh_hint = QLabel("未找到 ~/.ssh/config，请先创建（可空文件即可，Host 条目手动填）")
            ssh_hint.setStyleSheet("color:#8a7f63")
            gpt_form.addRow(ssh_hint)

        self.gpt_ssh_status = QLabel("未测试")
        self.gpt_ssh_status.setStyleSheet("color:#8a7f63")
        self.gpt_ssh_status.setWordWrap(True)
        test_btn = QPushButton("测试 SSH 连接")
        test_btn.clicked.connect(self._test_ssh)
        gpt_form.addRow(self.gpt_ssh_status, test_btn)

        self.gpt_local_port = QLineEdit(str(gpt_cfg.get("local_port", 9880)))
        gpt_form.addRow("本地端口", self.gpt_local_port)
        self.gpt_remote_port = QLineEdit(str(gpt_cfg.get("remote_port", 9880)))
        gpt_form.addRow("远程端口", self.gpt_remote_port)

        tunnel_btn = QPushButton("建立隧道（测试用）")
        tunnel_btn.clicked.connect(self._test_tunnel)
        self._tunnel: SSHTunnel | None = None
        gpt_form.addRow(tunnel_btn)
        voice_layout.addWidget(self._gpt_block)

        # === 阿里云百炼 TTS（独立容器，选择 aliyun 时显示）===
        from config import ALIYUN_TTS_DEFAULTS, ALIYUN_TTS_ENGINES
        self._aliyun_block = QWidget()
        aliyun_form = QFormLayout(self._aliyun_block)
        _tune_form(aliyun_form)
        aliyun_form.addRow(_section("ALIYUN BAILIAN TTS"))
        aliyun_cfg = {**ALIYUN_TTS_DEFAULTS, **(config.get("aliyun_tts") or {})}
        self.aliyun_api_key = QLineEdit(str(aliyun_cfg.get("api_key", "")))
        self.aliyun_api_key.setEchoMode(QLineEdit.Password)
        aliyun_form.addRow("API Key", self.aliyun_api_key)

        self.aliyun_voice_id = QLineEdit(str(aliyun_cfg.get("voice_id", "")))
        aliyun_form.addRow("音色 ID", self.aliyun_voice_id)

        self.aliyun_preferred_name = QLineEdit(str(aliyun_cfg.get("preferred_name", "amadeus_kurisu")))
        aliyun_form.addRow("克隆名称", self.aliyun_preferred_name)

        # 合成引擎（移植 amadeus src/lib/tts.ts:36-42）：CosyVoice v3.5-flash 默认快、Qwen3-TTS-VC 备选
        self.aliyun_engine = QComboBox()
        for label, value in ALIYUN_TTS_ENGINES:
            self.aliyun_engine.addItem(label, value)
        current_engine = str(aliyun_cfg.get("engine", ALIYUN_TTS_DEFAULTS["engine"]))
        idx = self.aliyun_engine.findData(current_engine)
        self.aliyun_engine.setCurrentIndex(max(idx, 0))
        aliyun_form.addRow("合成引擎", self.aliyun_engine)

        self.aliyun_status = QLabel(
            "已克隆" if aliyun_cfg.get("voice_cloned") else "未克隆"
        )
        self.aliyun_status.setStyleSheet("color:#8a7f63")
        self.aliyun_status.setWordWrap(True)
        self.aliyun_clone_btn = QPushButton("一键克隆红莉栖音色")
        self.aliyun_clone_btn.clicked.connect(self._on_clone_voice)
        aliyun_form.addRow(self.aliyun_status, self.aliyun_clone_btn)
        voice_layout.addWidget(self._aliyun_block)
        voice_layout.addStretch()
        tabs.addTab(_scroll_page(voice_page), "语音合成")
        tabs.addTab(_scroll_page(asr_page), "语音输入")
        self.tts_provider.currentIndexChanged.connect(self._on_tts_provider_changed)

        # === Agent 模式（2026-08-15 agent-mode spec §4.4）===
        from config import AGENT_ROUTER_DEFAULTS, HARNESS_DEFAULTS, HERMES_DEFAULTS, OPENCLAW_DEFAULTS
        agent_page = QWidget()
        agent_layout = QVBoxLayout(agent_page)
        agent_layout.setContentsMargins(0, 0, 0, 0)
        agent_layout.setSpacing(0)
        agent_form = QFormLayout()
        _tune_form(agent_form)
        agent_form.addRow(_section("AGENT ROUTER"))
        router_cfg = {**AGENT_ROUTER_DEFAULTS, **(config.get("agent_router") or {})}
        self.agent_mode = QComboBox()
        self.agent_mode.addItem("本地直连（默认）", "chat")
        self.agent_mode.addItem("DeepSeek Harness SDK", "harness")
        self.agent_mode.addItem("Hermes 网关（deepseek 模式）", "hermes")
        self.agent_mode.addItem("OpenClaw 网关（本地代理）", "openclaw")
        self.agent_mode.addItem("DeepSeek 直连", "deepseek")
        self.agent_mode.addItem("codex 子进程", "codex")
        self.agent_mode.addItem("自动分流（gate）", "auto")
        idx = self.agent_mode.findData(str(router_cfg.get("mode", "chat")))
        self.agent_mode.setCurrentIndex(max(idx, 0))
        agent_form.addRow("Agent 模式", self.agent_mode)
        self._agent_hint = QLabel("本地直连：使用「直连模型」tab 的配置。")
        self._agent_hint.setStyleSheet("color:#8a7f63")
        agent_form.addRow(self._agent_hint)

        # 自动分流（Ollama 小模型，独立开关，优先于 Agent 模式）
        self.auto_route = QCheckBox("自动分流（Ollama 小模型）")
        self.auto_route.setChecked(bool(router_cfg.get("auto_route", False)))
        agent_form.addRow(self.auto_route)
        agent_layout.addLayout(agent_form)

        self._auto_block = QWidget()
        auto_form = QFormLayout(self._auto_block)
        _tune_form(auto_form)
        auto_form.addRow(_section("AUTO ROUTE (OLLAMA)"))
        auto_targets = list(router_cfg.get("auto_targets") or ["local", "harness"])
        self.auto_target_local = QCheckBox("本地直连")
        self.auto_target_local.setChecked("local" in auto_targets)
        self.auto_target_harness = QCheckBox("DeepSeek Harness")
        self.auto_target_harness.setChecked("harness" in auto_targets)
        auto_form.addRow(self.auto_target_local)
        auto_form.addRow(self.auto_target_harness)
        ollama_cfg = dict(router_cfg.get("ollama") or {})
        self.ollama_base_url = QLineEdit(str(ollama_cfg.get("base_url", "http://127.0.0.1:11434")))
        self.ollama_model = QLineEdit(str(ollama_cfg.get("model", "qwen2.5:0.5b")))
        auto_form.addRow("Ollama Base URL", self.ollama_base_url)
        auto_form.addRow("Ollama Model", self.ollama_model)
        agent_layout.addWidget(self._auto_block)
        self.auto_route.toggled.connect(self._on_auto_route_toggled)
        self._on_auto_route_toggled()

        # Hermes 块
        self._hermes_block = QWidget()
        hermes_form = QFormLayout(self._hermes_block)
        _tune_form(hermes_form)
        hermes_form.addRow(_section("HERMES GATEWAY"))
        hermes_cfg = {**HERMES_DEFAULTS, **(config.get("hermes") or {})}
        self.hermes_key = QLineEdit(str(hermes_cfg.get("api_key", "")))
        self.hermes_key.setEchoMode(QLineEdit.Password)
        hermes_form.addRow("Hermes API Key", self.hermes_key)
        self.hermes_status = QLabel("未检测")
        self.hermes_status.setStyleSheet("color:#8a7f63")
        hermes_btn = QPushButton("检测 Hermes 网关")
        hermes_btn.clicked.connect(self._probe_hermes)
        hermes_form.addRow(self.hermes_status, hermes_btn)
        agent_layout.addWidget(self._hermes_block)

        # OpenClaw 块（对话后端 + CUA 工具共用同一 Gateway 配置）
        self._openclaw_block = QWidget()
        openclaw_form = QFormLayout(self._openclaw_block)
        _tune_form(openclaw_form)
        openclaw_form.addRow(_section("OPENCLAW GATEWAY"))
        openclaw_cfg = {**OPENCLAW_DEFAULTS, **(config.get("openclaw") or {})}
        self.openclaw_enabled = QCheckBox("启用 CUA 工具（operate_gui 委托 OpenClaw 操作桌面）")
        self.openclaw_enabled.setChecked(bool(openclaw_cfg.get("enabled", False)))
        openclaw_form.addRow(self.openclaw_enabled)
        self.openclaw_base_url = QLineEdit(str(openclaw_cfg.get("base_url", "http://127.0.0.1:18789")))
        openclaw_form.addRow("OpenClaw Base URL", self.openclaw_base_url)
        self.openclaw_token = QLineEdit(str(openclaw_cfg.get("token", "")))
        self.openclaw_token.setEchoMode(QLineEdit.Password)
        openclaw_form.addRow("OpenClaw Token", self.openclaw_token)
        self.openclaw_model = QLineEdit(str(openclaw_cfg.get("model", "openclaw/default")))
        openclaw_form.addRow("OpenClaw 代理（model）", self.openclaw_model)
        self.openclaw_timeout = QLineEdit(str(openclaw_cfg.get("timeout", 120)))
        openclaw_form.addRow("CUA 超时（秒）", self.openclaw_timeout)
        self.openclaw_autostart = QCheckBox("网关离线时自动拉起（openclaw gateway）")
        self.openclaw_autostart.setChecked(bool(openclaw_cfg.get("autostart", True)))
        openclaw_form.addRow(self.openclaw_autostart)
        self.openclaw_status = QLabel("未检测")
        self.openclaw_status.setStyleSheet("color:#8a7f63")
        openclaw_btn = QPushButton("检测 OpenClaw 网关")
        openclaw_btn.clicked.connect(self._probe_openclaw)
        openclaw_form.addRow(self.openclaw_status, openclaw_btn)
        agent_layout.addWidget(self._openclaw_block)

        # DeepSeek 块
        self._deepseek_block = QWidget()
        deepseek_form = QFormLayout(self._deepseek_block)
        _tune_form(deepseek_form)
        deepseek_form.addRow(_section("DEEPSEEK DIRECT"))
        deepseek_cfg = {**AGENT_ROUTER_DEFAULTS["deepseek"], **(config.get("deepseek") or {})}
        self.deepseek_base_url = QLineEdit(str(deepseek_cfg.get("base_url", "http://127.0.0.1:8642")))
        self.deepseek_api_key = QLineEdit(str(deepseek_cfg.get("api_key", "")))
        self.deepseek_api_key.setEchoMode(QLineEdit.Password)
        self.deepseek_model = QLineEdit(str(deepseek_cfg.get("model", "deepseek-v3.1")))
        deepseek_form.addRow("DeepSeek Base URL", self.deepseek_base_url)
        deepseek_form.addRow("DeepSeek API Key", self.deepseek_api_key)
        deepseek_form.addRow("DeepSeek Model", self.deepseek_model)
        agent_layout.addWidget(self._deepseek_block)

        # Harness 块
        self._harness_block = QWidget()
        harness_form = QFormLayout(self._harness_block)
        _tune_form(harness_form)
        harness_form.addRow(_section("HARNESS SDK"))
        harness_cfg = {**AGENT_ROUTER_DEFAULTS.get("harness", {}), **(config.get("harness") or {})}
        self.harness_provider = QComboBox()
        self.harness_provider.addItem("DeepSeek 官方", "deepseek-official")
        self.harness_provider.addItem("Custom OpenAI", "custom-openai")
        idx = self.harness_provider.findData(str(harness_cfg.get("provider", "deepseek-official")))
        self.harness_provider.setCurrentIndex(max(idx, 0))
        self.harness_runtime_bin = QLineEdit(str(harness_cfg.get("runtime_bin", "")))
        harness_form.addRow("Harness Provider", self.harness_provider)
        harness_form.addRow("Harness Runtime Bin", self.harness_runtime_bin)
        self.harness_model = QLineEdit(str(harness_cfg.get("model", HARNESS_DEFAULTS["model"])))
        self.harness_base_url = QLineEdit(str(harness_cfg.get("base_url", "")))
        self.harness_api_key = QLineEdit(str(harness_cfg.get("api_key", "")))
        self.harness_api_key.setEchoMode(QLineEdit.Password)
        self.harness_cwd = QLineEdit(str(harness_cfg.get("cwd", "")))
        self.harness_session_root = QLineEdit(str(harness_cfg.get("session_root", "")))
        self.harness_cordis = QLineEdit(str(harness_cfg.get("cordis", "")))
        self.harness_timeout = QLineEdit(str(harness_cfg.get("request_timeout_seconds", HARNESS_DEFAULTS["request_timeout_seconds"])))
        self.harness_sandbox_mode = QComboBox()
        self.harness_sandbox_mode.addItem("只读（read-only）", "read-only")
        self.harness_sandbox_mode.addItem("工作区可写（workspace-write）", "workspace-write")
        self.harness_sandbox_mode.addItem("完全访问（danger-full-access）", "danger-full-access")
        idx = self.harness_sandbox_mode.findData(str(harness_cfg.get("sandbox_mode", HARNESS_DEFAULTS["sandbox_mode"])))
        self.harness_sandbox_mode.setCurrentIndex(max(idx, 0))
        self.harness_approval_policy = QComboBox()
        self.harness_approval_policy.addItem("每次询问（ask）", "ask")
        self.harness_approval_policy.addItem("从不询问（never，自动拒绝）", "never")
        idx = self.harness_approval_policy.findData(str(harness_cfg.get("approval_policy", HARNESS_DEFAULTS["approval_policy"])))
        self.harness_approval_policy.setCurrentIndex(max(idx, 0))
        self.harness_enable_web = QCheckBox("启用 Web")
        self.harness_enable_web.setChecked(bool(harness_cfg.get("enable_web", True)))
        self.harness_enable_plan_mode = QCheckBox("启用 Plan Mode")
        self.harness_enable_plan_mode.setChecked(bool(harness_cfg.get("enable_plan_mode", True)))
        self.harness_enable_workflow = QCheckBox("启用 Workflow")
        self.harness_enable_workflow.setChecked(bool(harness_cfg.get("enable_workflow", True)))
        self.harness_enable_editor = QCheckBox("启用 Editor")
        self.harness_enable_editor.setChecked(bool(harness_cfg.get("enable_editor", True)))
        self.harness_enable_subagent_fork = QCheckBox("启用 Subagent Fork")
        self.harness_enable_subagent_fork.setChecked(bool(harness_cfg.get("enable_subagent_fork", True)))
        self.harness_enable_sandbox = QCheckBox("启用 Sandbox")
        self.harness_enable_sandbox.setChecked(bool(harness_cfg.get("enable_sandbox", True)))
        self.harness_enable_commands = QCheckBox("启用 Commands")
        self.harness_enable_commands.setChecked(bool(harness_cfg.get("enable_commands", True)))
        self.harness_enable_terminal = QCheckBox("启用 Terminal")
        self.harness_enable_terminal.setChecked(bool(harness_cfg.get("enable_terminal", False)))
        harness_form.addRow("Harness Model", self.harness_model)
        harness_form.addRow("Harness Base URL", self.harness_base_url)
        harness_form.addRow("Harness API Key", self.harness_api_key)
        harness_form.addRow("Harness CWD", self.harness_cwd)
        harness_form.addRow("Harness Session Root", self.harness_session_root)
        harness_form.addRow("Harness Cordis", self.harness_cordis)
        harness_form.addRow("Harness Timeout", self.harness_timeout)
        harness_form.addRow("Sandbox Mode", self.harness_sandbox_mode)
        harness_form.addRow("Approval Policy", self.harness_approval_policy)
        harness_form.addRow(self.harness_enable_web)
        harness_form.addRow(self.harness_enable_plan_mode)
        harness_form.addRow(self.harness_enable_workflow)
        harness_form.addRow(self.harness_enable_editor)
        harness_form.addRow(self.harness_enable_subagent_fork)
        harness_form.addRow(self.harness_enable_sandbox)
        harness_form.addRow(self.harness_enable_commands)
        harness_form.addRow(self.harness_enable_terminal)
        agent_layout.addWidget(self._harness_block)

        # Codex 块
        self._codex_block = QWidget()
        codex_form = QFormLayout(self._codex_block)
        _tune_form(codex_form)
        codex_form.addRow(_section("CODEX SUBPROCESS"))
        codex_cfg = {**AGENT_ROUTER_DEFAULTS["codex"], **(router_cfg.get("codex") or {})}
        self.codex_sandbox = QComboBox()
        self.codex_sandbox.addItem("只读（默认）", "read-only")
        self.codex_sandbox.addItem("可写工作区", "workspace-write")
        idx = self.codex_sandbox.findData(str(codex_cfg.get("sandbox", "read-only")))
        self.codex_sandbox.setCurrentIndex(max(idx, 0))
        codex_form.addRow("codex 沙箱", self.codex_sandbox)
        agent_layout.addWidget(self._codex_block)
        agent_layout.addStretch()
        tabs.addTab(_scroll_page(agent_page), "Agent 模式")
        self.agent_mode.currentIndexChanged.connect(self._on_agent_mode_changed)

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

        # === 屏幕感知（普通聊天，core/screen_context.py，对标 airi see-your-screen）===
        from config import SCREEN_AWARENESS_DEFAULTS
        sa_cfg = {**SCREEN_AWARENESS_DEFAULTS, **(config.get("screen_awareness") or {})}
        companion_form.addRow(_section("屏幕感知 · 聊天"))
        self.screen_awareness_enabled = QCheckBox("聊天时附加当前屏幕描述（默认关，隐私）")
        self.screen_awareness_enabled.setChecked(bool(sa_cfg.get("enabled", False)))
        companion_form.addRow(self.screen_awareness_enabled)
        self.screen_vision_endpoint = QLineEdit(str(sa_cfg.get("vision_endpoint", "")))
        self.screen_vision_endpoint.setPlaceholderText("OpenAI 兼容端点，留空回退电话模式视觉端点")
        companion_form.addRow("视觉端点", self.screen_vision_endpoint)
        self.screen_vision_api_key = QLineEdit(str(sa_cfg.get("vision_api_key", "")))
        self.screen_vision_api_key.setEchoMode(QLineEdit.Password)
        self.screen_vision_api_key.setPlaceholderText("留空回退电话模式 key")
        companion_form.addRow("视觉 API Key", self.screen_vision_api_key)
        try:
            _sa_interval = str(sa_cfg.get("interval_seconds", 120))
        except (TypeError, ValueError):
            _sa_interval = "120"
        self.screen_interval = QLineEdit(_sa_interval)
        self.screen_interval.setPlaceholderText("描述缓存有效期（秒），避免逐条消息重复请求")
        companion_form.addRow("缓存秒数", self.screen_interval)

        tabs.addTab(_scroll_page(companion_page), "Companion")

        # === 消息接入（QQ / 微信，docs/PRD-im-message-notify.md）===
        from config import IM_DEFAULTS
        im_page = QWidget()
        im_form = QFormLayout(im_page)
        _tune_form(im_form)
        im_form.addRow(_section("IM · QQ（ONEBOT 11 / NAPCAT）"))
        im_cfg = {**IM_DEFAULTS, **(config.get("im") or {})}
        qq_cfg = {**IM_DEFAULTS["qq"], **(im_cfg.get("qq") or {})}
        self.im_qq_enabled = QCheckBox("启用 QQ 消息接入（需先启动 NapCat 并登录）")
        self.im_qq_enabled.setChecked(bool(qq_cfg.get("enabled", False)))
        im_form.addRow(self.im_qq_enabled)
        self.im_ws_url = QLineEdit(str(qq_cfg.get("ws_url", "")))
        im_form.addRow("WebSocket 地址", self.im_ws_url)
        self.im_group_at_only = QCheckBox("群消息只通知 @我（否则全部通知）")
        self.im_group_at_only.setChecked(bool(qq_cfg.get("group_at_only", True)))
        im_form.addRow(self.im_group_at_only)
        self.im_keywords = QLineEdit("、".join(qq_cfg.get("keywords") or []))
        self.im_keywords.setPlaceholderText("关键词、顿号分隔（群消息命中也通知，可留空）")
        im_form.addRow("群关键词白名单", self.im_keywords)

        im_form.addRow(_section("IM · 通知"))
        notify_cfg = {**IM_DEFAULTS["notify"], **(im_cfg.get("notify") or {})}
        self.im_notify_bubble = QCheckBox("桌宠气泡通知")
        self.im_notify_bubble.setChecked(bool(notify_cfg.get("bubble", True)))
        im_form.addRow(self.im_notify_bubble)
        self.im_notify_tray = QCheckBox("托盘系统通知")
        self.im_notify_tray.setChecked(bool(notify_cfg.get("tray", True)))
        im_form.addRow(self.im_notify_tray)
        self.im_notify_tts = QCheckBox("TTS 播报（默认关，注意外放隐私）")
        self.im_notify_tts.setChecked(bool(notify_cfg.get("tts", False)))
        im_form.addRow(self.im_notify_tts)
        im_qh = {**IM_DEFAULTS["quiet_hours"], **(im_cfg.get("quiet_hours") or {})}
        self.im_quiet_start = QLineEdit(str(im_qh.get("start", "23:00")))
        self.im_quiet_end = QLineEdit(str(im_qh.get("end", "08:00")))
        im_form.addRow("免打扰开始", self.im_quiet_start)
        im_form.addRow("免打扰结束", self.im_quiet_end)

        im_form.addRow(_section("IM · 状态"))
        self.im_status = QLabel("（保存并重启后生效；下方可测试连通性）")
        self.im_status.setStyleSheet("color:#8a7f63")
        self.im_status.setWordWrap(True)
        im_form.addRow(self.im_status)
        im_test_btn = QPushButton("测试连接")
        im_test_btn.clicked.connect(self._test_im_connection)
        im_form.addRow(im_test_btn)
        im_form.addRow(QLabel("提示：非官方协议，存在账号风控风险，建议先用小号验证。"))
        tabs.addTab(_scroll_page(im_page), "消息接入")


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
        self.title_bar = CrtTitleBar(
            "⌈ Ａｍａｄｅｕｓ Ｓｅｔｔｉｎｇｓ ⌋",
            "tait-crt-interface-skill",
            self,
            self.reject,
        )
        # 相位签名短语轮换（fauux ⑧⑬：多语言短语交替）
        self._sig_phrases = (
            "tait-crt-interface-skill", "私の声、聞こえる？",
            "[make me sad]", "wire ESTABLISHED · ch 1",
        )
        self._sig_index = 0
        self._sig_timer = QTimer(self)
        self._sig_timer.setInterval(1200)
        self._sig_timer.timeout.connect(self._cycle_signature)
        self._sig_timer.start()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        layout.addWidget(self.title_bar)
        layout.addWidget(tabs)

        # 底部左侧滚动台词（fauux 长句 marquee）：与 Save/Cancel 同一行，
        # 占据按钮左侧的全部剩余空间，文本右→左缓慢滚动循环
        self._ticker_bar = QWidget(self)
        self._ticker_bar.setStyleSheet("background:transparent")
        self._ticker_bar.setFixedHeight(32)
        self._ticker_label = QLabel(_TICKER_TEXT, self._ticker_bar)
        self._ticker_label.setStyleSheet(
            "color:#8a7f63;background:transparent;"
            "font:700 24px 'Poiret One','Consolas','Microsoft YaHei';"
            "text-transform:uppercase;white-space:nowrap"
        )
        self._ticker_label.adjustSize()
        self._ticker_w = self._ticker_label.width()
        self._ticker_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._ticker_bar.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._ticker_x = 0
        self._ticker_timer = QTimer(self)
        self._ticker_timer.setInterval(30)
        self._ticker_timer.timeout.connect(self._tick_ticker)
        self._ticker_timer.start()

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(10)
        bottom_row.addWidget(self._ticker_bar, 1)
        bottom_row.addWidget(buttons, 0)
        layout.addLayout(bottom_row)
        self._tick_ticker()

        # CRT 特效叠加层：扫描线 + 暗角（透明，不拦截鼠标）
        from ui.widgets.crt_overlay import CrtOverlay
        self._crt = CrtOverlay(self, scanlines=True, vignette=True, noise=False)
        self._crt.raise_()

        # 按当前选择初始化各服务配置块的可见性
        self._on_tts_provider_changed()
        self._on_agent_mode_changed()

    def _cycle_signature(self) -> None:
        """轮换标题栏右侧的相位签名短语（fauux [make me sad] 交互）。"""
        self._sig_index = (self._sig_index + 1) % len(self._sig_phrases)
        self.title_bar.set_signature(self._sig_phrases[self._sig_index])

    def _tick_ticker(self) -> None:
        """底部左侧滚动台词推进：文本自右向左缓慢滚动，出屏后从头进入。"""
        bar_w = max(self._ticker_bar.width(), 200)
        self._ticker_x -= 1
        if self._ticker_x + self._ticker_w < 0:
            self._ticker_x = bar_w
        self._ticker_label.move(self._ticker_x, (self._ticker_bar.height() - self._ticker_label.height()) // 2)

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

    def _on_tts_provider_changed(self) -> None:
        """切换 TTS provider 时只显示对应后端的配置项。"""
        provider = self.tts_provider.currentData()
        self._gpt_block.setVisible(provider == "gpt_sovits")
        self._aliyun_block.setVisible(provider == "aliyun")

    def _on_agent_mode_changed(self) -> None:
        """切换 Agent 模式时只显示对应后端的配置项；auto 显示全部。"""
        mode = self.agent_mode.currentData()
        show_all = mode == "auto"
        self._agent_hint.setVisible(mode == "chat")
        self._hermes_block.setVisible(mode == "hermes" or show_all)
        self._openclaw_block.setVisible(mode == "openclaw" or show_all)
        self._deepseek_block.setVisible(mode == "deepseek" or show_all)
        self._harness_block.setVisible(mode == "harness" or show_all)
        self._codex_block.setVisible(mode == "codex" or show_all)

    def _on_auto_route_toggled(self) -> None:
        """自动分流开关：显示/隐藏 Ollama 配置与模式勾选。"""
        self._auto_block.setVisible(self.auto_route.isChecked())

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

    def _probe_openclaw(self) -> None:
        """同步探测 OpenClaw 网关 /v1/models（2s 超时，设置页内可接受）。"""
        from config import OPENCLAW_DEFAULTS
        from core.openclaw_client import probe_gateway
        cfg = {**OPENCLAW_DEFAULTS, **(load_config().get("openclaw") or {})}
        # 优先用输入框当前值，允许保存前先试新地址/token
        base_url = self.openclaw_base_url.text().strip() or str(cfg.get("base_url"))
        token = self.openclaw_token.text().strip()
        self.openclaw_status.setText("检测中…")
        QApplication.processEvents()
        ok = probe_gateway(base_url, token)
        self.openclaw_status.setText("在线" if ok else "离线")
        self.openclaw_status.setStyleSheet("color:#34c759" if ok else "color:#d2738a")

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

    def _test_ssh(self) -> None:
        """测试 SSH 连接（不建隧道，只探测连通性）。"""
        from core.ssh_tunnel import SSHTunnel
        host_alias = self.gpt_ssh_host.currentData()
        if not host_alias:
            self.gpt_ssh_status.setText("请先选择 SSH Host")
            self.gpt_ssh_status.setStyleSheet("color:#d2738a")
            return
        host_obj = next((h for h in self._ssh_hosts if h.host == host_alias), None)
        if host_obj is None:
            self.gpt_ssh_status.setText("Host 信息丢失，请重开设置")
            self.gpt_ssh_status.setStyleSheet("color:#d2738a")
            return
        self.gpt_ssh_status.setText("测试中...")
        self.gpt_ssh_status.setStyleSheet("color:#c1b492")
        QApplication.processEvents()
        tunnel = SSHTunnel(host_obj, local_port=9880, remote_port=9880)
        status = tunnel.test(timeout=5)
        if status.ok:
            self.gpt_ssh_status.setText(f"✓ {status.message}")
            self.gpt_ssh_status.setStyleSheet("color:#6abf69")
        else:
            self.gpt_ssh_status.setText(f"✗ {status.message}")
            self.gpt_ssh_status.setStyleSheet("color:#d2738a")

    def _test_tunnel(self) -> None:
        """建立隧道并探测 GPT-SoVITS API 是否可用。"""
        from core.ssh_tunnel import SSHTunnel
        from core.gpt_sovits_client import KurisuTTS
        host_alias = self.gpt_ssh_host.currentData()
        if not host_alias:
            self.gpt_ssh_status.setText("请先选择 SSH Host")
            self.gpt_ssh_status.setStyleSheet("color:#d2738a")
            return
        host_obj = next((h for h in self._ssh_hosts if h.host == host_alias), None)
        if host_obj is None:
            return
        try:
            local_port = int(self.gpt_local_port.text().strip())
            remote_port = int(self.gpt_remote_port.text().strip())
        except ValueError:
            self.gpt_ssh_status.setText("端口必须是数字")
            self.gpt_ssh_status.setStyleSheet("color:#d2738a")
            return

        if self._tunnel is not None:
            self._tunnel.stop()
            self._tunnel = None
        self.gpt_ssh_status.setText("建立隧道中...")
        self.gpt_ssh_status.setStyleSheet("color:#c1b492")
        QApplication.processEvents()

        self._tunnel = SSHTunnel(host_obj, local_port=local_port, remote_port=remote_port)
        status = self._tunnel.start()
        if not status.ok:
            self.gpt_ssh_status.setText(f"✗ {status.message}")
            self.gpt_ssh_status.setStyleSheet("color:#d2738a")
            self._tunnel = None
            return

        # 探测 GPT-SoVITS API
        import urllib.request
        url = f"http://127.0.0.1:{local_port}/docs"
        try:
            with urllib.request.urlopen(url, timeout=3.0) as resp:
                if resp.status == 200:
                    self.gpt_ssh_status.setText(f"✓ 隧道+API 可用（{status.message}）")
                    self.gpt_ssh_status.setStyleSheet("color:#6abf69")
                else:
                    self.gpt_ssh_status.setText(f"✗ API 返回 {resp.status}")
                    self.gpt_ssh_status.setStyleSheet("color:#d2738a")
        except Exception as e:
            self.gpt_ssh_status.setText(f"✗ 隧道已建立但 API 不可达：{e}（服务器上 GPT-SoVITS 启动了吗？）")
            self.gpt_ssh_status.setStyleSheet("color:#d2738a")

    def _on_clone_voice(self) -> None:
        api_key = self.aliyun_api_key.text().strip()
        if not api_key:
            self.aliyun_status.setText("请先填写 API Key")
            self.aliyun_status.setStyleSheet("color:#d2738a")
            return
        self.aliyun_clone_btn.setEnabled(False)
        self.aliyun_status.setText("克隆中...")
        self.aliyun_status.setStyleSheet("color:#c1b492")
        QApplication.processEvents()
        preferred_name = self.aliyun_preferred_name.text().strip() or "amadeus_kurisu"
        # 克隆 target_model 固定 qwen3-tts-vc-2026-01-22（与 engine 解耦，与 amadeus src/app/api/tts/clone/route.ts:88 对齐）
        model = "qwen3-tts-vc-2026-01-22"

        def worker() -> None:
            try:
                from config import ALIYUN_TTS_DEFAULTS, resources_path
                from core.aliyun_tts_client import AliyunTTS

                cfg = {**ALIYUN_TTS_DEFAULTS, **(load_config().get("aliyun_tts") or {})}
                ref_audio = resources_path(str(cfg.get("ref_audio", "/crs_1393.wav")))
                ref_text = str(cfg.get("ref_text", "") or "").strip() or None
                tts_client = AliyunTTS(api_key)
                voice_id, fallback_mode, fallback_reason = tts_client.clone_voice(
                    ref_audio,
                    preferred_name=preferred_name,
                    target_model=model or str(ALIYUN_TTS_DEFAULTS["model"]),
                    ref_text=ref_text,
                )
                if voice_id:
                    validation_audio = tts_client.synthesize(
                        "こんにちは、音声テストです。",
                        voice_id,
                        text_lang="ja",
                        model=model,
                    )
                    if not validation_audio:
                        raise RuntimeError("音色已创建，但测试合成未返回音频，请重新克隆")
                    QMetaObject.invokeMethod(
                        self, "_on_clone_done", Qt.QueuedConnection, Q_ARG(str, voice_id)
                    )
                    if fallback_mode:
                        reason = fallback_reason or "音频与文本不匹配或音频质量不佳"
                        QMetaObject.invokeMethod(
                            self, "_on_clone_warn", Qt.QueuedConnection, Q_ARG(str, reason)
                        )
                else:
                    QMetaObject.invokeMethod(
                        self, "_on_clone_failed", Qt.QueuedConnection,
                        Q_ARG(str, "克隆失败：未返回音色 ID"),
                    )
            except Exception as exc:
                QMetaObject.invokeMethod(
                    self, "_on_clone_failed", Qt.QueuedConnection, Q_ARG(str, str(exc))
                )

        threading.Thread(target=worker, daemon=True).start()

    @Slot(str)
    def _on_clone_done(self, voice_id: str) -> None:
        self.aliyun_voice_id.setText(voice_id)
        self.aliyun_status.setText(f"克隆成功：{voice_id}")
        self.aliyun_status.setStyleSheet("color:#6abf69")
        self.aliyun_clone_btn.setEnabled(True)

    @Slot(str)
    def _on_clone_failed(self, message: str) -> None:
        self.aliyun_status.setText(message)
        self.aliyun_status.setStyleSheet("color:#d2738a")
        self.aliyun_clone_btn.setEnabled(True)

    @Slot(str)
    def _on_clone_warn(self, reason: str) -> None:
        self.aliyun_status.setText(f"克隆成功（降级模式）：{reason}")
        self.aliyun_status.setStyleSheet("color:#d8a53f")

    def _test_im_connection(self) -> None:
        """后台线程快速探测 NapCat WS 端口连通性，结果回 UI 线程。"""
        url = self.im_ws_url.text().strip()

        def worker() -> None:
            import asyncio
            import websockets

            try:
                async def _probe() -> None:
                    async with websockets.connect(url, open_timeout=5):
                        pass
                asyncio.run(_probe())
                QMetaObject.invokeMethod(self.im_status, "setText",
                                         Q_ARG(str, f"✓ 连接成功：{url}"))
            except Exception as exc:  # noqa: BLE001
                QMetaObject.invokeMethod(self.im_status, "setText",
                                         Q_ARG(str, f"✗ 连接失败：{exc}"))

        self.im_status.setText("测试中…")
        threading.Thread(target=worker, daemon=True).start()

    def _save(self) -> None:
        config = load_config()
        config.update({
            "endpoint": self.endpoint.text().strip(), "api_key": self.api_key.text().strip(),
            "model": self.model.text().strip(), "tts_enabled": self.tts_enabled.isChecked(),
            "tts_provider": self.tts_provider.currentData(),
            "tts_rate": self.tts_rate.currentIndex(), "asr_endpoint": self.asr_endpoint.text().strip(),
            "asr_api_key": self.asr_key.text().strip(), "asr_model": self.asr_model.text().strip(),
            "mic_device_index": self.mic_device.currentData(),
            "aec": {
                "enabled": self.aec_enabled.isChecked(),
                "filter_len_ms": self.aec_filter_len.currentData(),
                "mu": self.aec_mu.currentData(),
                "align_delay_ms": self.aec_align.currentData(),
                "nlp_gain": self.aec_nlp.currentData(),
            },
            "version_check_url": self.version_check_url.text().strip(),
        })
        from config import AGENT_ROUTER_DEFAULTS, HARNESS_DEFAULTS, HERMES_DEFAULTS, OPENCLAW_DEFAULTS
        router_cfg = {**AGENT_ROUTER_DEFAULTS, **(config.get("agent_router") or {})}
        codex_cfg = {**AGENT_ROUTER_DEFAULTS["codex"], **(router_cfg.get("codex") or {})}
        codex_cfg["sandbox"] = self.codex_sandbox.currentData()
        _ollama_cfg = dict(router_cfg.get("ollama") or {})
        try:
            _ollama_timeout = float(_ollama_cfg.get("timeout", 30))
        except (TypeError, ValueError):
            _ollama_timeout = 30.0
        auto_targets = []
        if self.auto_target_local.isChecked():
            auto_targets.append("local")
        if self.auto_target_harness.isChecked():
            auto_targets.append("harness")
        config["agent_router"] = {
            "mode": self.agent_mode.currentData(),
            "codex": codex_cfg,
            "auto_route": self.auto_route.isChecked(),
            "auto_targets": auto_targets,
            "ollama": {
                "base_url": self.ollama_base_url.text().strip(),
                "model": self.ollama_model.text().strip(),
                "timeout": _ollama_timeout,
            },
        }
        hermes_cfg = {**HERMES_DEFAULTS, **(config.get("hermes") or {})}
        hermes_cfg["api_key"] = self.hermes_key.text().strip()
        config["hermes"] = hermes_cfg
        openclaw_cfg = {**OPENCLAW_DEFAULTS, **(config.get("openclaw") or {})}
        openclaw_cfg["enabled"] = self.openclaw_enabled.isChecked()
        openclaw_cfg["base_url"] = self.openclaw_base_url.text().strip()
        openclaw_cfg["token"] = self.openclaw_token.text().strip()
        openclaw_cfg["model"] = self.openclaw_model.text().strip()
        try:
            openclaw_cfg["timeout"] = float(self.openclaw_timeout.text().strip())
        except ValueError:
            openclaw_cfg["timeout"] = float(OPENCLAW_DEFAULTS["timeout"])
        openclaw_cfg["autostart"] = self.openclaw_autostart.isChecked()
        config["openclaw"] = openclaw_cfg
        deepseek_cfg = {**AGENT_ROUTER_DEFAULTS["deepseek"], **(config.get("deepseek") or {})}
        deepseek_cfg["base_url"] = self.deepseek_base_url.text().strip()
        deepseek_cfg["api_key"] = self.deepseek_api_key.text().strip()
        config["deepseek"] = deepseek_cfg
        deepseek_cfg["model"] = self.deepseek_model.text().strip()
        config["deepseek"] = deepseek_cfg
        harness_cfg = {**AGENT_ROUTER_DEFAULTS.get("harness", {}), **(config.get("harness") or {})}
        harness_cfg["provider"] = self.harness_provider.currentData()
        harness_cfg["runtime_bin"] = self.harness_runtime_bin.text().strip()
        harness_cfg["model"] = self.harness_model.text().strip()
        harness_cfg["base_url"] = self.harness_base_url.text().strip()
        harness_cfg["api_key"] = self.harness_api_key.text().strip()
        harness_cfg["cwd"] = self.harness_cwd.text().strip()
        harness_cfg["session_root"] = self.harness_session_root.text().strip()
        harness_cfg["cordis"] = self.harness_cordis.text().strip()
        try:
            harness_cfg["request_timeout_seconds"] = float(self.harness_timeout.text().strip())
        except ValueError:
            harness_cfg["request_timeout_seconds"] = float(HARNESS_DEFAULTS["request_timeout_seconds"])
        harness_cfg["sandbox_mode"] = self.harness_sandbox_mode.currentData()
        harness_cfg["approval_policy"] = self.harness_approval_policy.currentData()
        harness_cfg["enable_web"] = self.harness_enable_web.isChecked()
        harness_cfg["enable_plan_mode"] = self.harness_enable_plan_mode.isChecked()
        harness_cfg["enable_workflow"] = self.harness_enable_workflow.isChecked()
        harness_cfg["enable_editor"] = self.harness_enable_editor.isChecked()
        harness_cfg["enable_subagent_fork"] = self.harness_enable_subagent_fork.isChecked()
        harness_cfg["enable_sandbox"] = self.harness_enable_sandbox.isChecked()
        harness_cfg["enable_commands"] = self.harness_enable_commands.isChecked()
        harness_cfg["enable_terminal"] = self.harness_enable_terminal.isChecked()
        config["harness"] = harness_cfg
        # 根据 enable_* 开关 + 沙箱/审批语义动态生成 cordis，写入 data/harness/cordis.full.yml
        try:
            from core.cordis_builder import write_generated_cordis
            write_generated_cordis(harness_cfg)
        except Exception:
            pass  # 生成失败不阻塞保存；harness 运行时回退到内置全量模板
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
        # 屏幕感知配置（core/screen_context.py）
        from config import SCREEN_AWARENESS_DEFAULTS
        try:
            _sa_seconds = int(self.screen_interval.text().strip())
        except ValueError:
            _sa_seconds = int(SCREEN_AWARENESS_DEFAULTS["interval_seconds"])
        config["screen_awareness"] = {
            "enabled": self.screen_awareness_enabled.isChecked(),
            "interval_seconds": max(15, _sa_seconds),
            "vision_endpoint": self.screen_vision_endpoint.text().strip(),
            "vision_api_key": self.screen_vision_api_key.text().strip(),
            # vision_model 固定默认（DeepSeek 无视觉，需 GPT-4o 级），暂不暴露 UI
        }
        # IM 消息接入配置（docs/PRD-im-message-notify.md）
        from config import IM_DEFAULTS
        im_cfg = {**IM_DEFAULTS, **(config.get("im") or {})}
        im_cfg["qq"] = {
            "enabled": self.im_qq_enabled.isChecked(),
            "ws_url": self.im_ws_url.text().strip() or IM_DEFAULTS["qq"]["ws_url"],
            "group_at_only": self.im_group_at_only.isChecked(),
            "keywords": [k.strip() for k in self.im_keywords.text().replace("，", "、").split("、") if k.strip()],
        }
        im_cfg["notify"] = {
            "bubble": self.im_notify_bubble.isChecked(),
            "tray": self.im_notify_tray.isChecked(),
            "tts": self.im_notify_tts.isChecked(),
        }
        im_cfg["quiet_hours"] = {
            "start": self.im_quiet_start.text().strip() or "23:00",
            "end": self.im_quiet_end.text().strip() or "08:00",
        }
        config["im"] = im_cfg
        # GPT-SoVITS 运行模式配置
        try:
            local_port = int(self.gpt_local_port.text().strip())
            remote_port = int(self.gpt_remote_port.text().strip())
        except ValueError:
            local_port, remote_port = 9880, 9880
        config["gpt_sovits"] = {
            "mode": self.gpt_mode.currentData(),
            "ssh_host": self.gpt_ssh_host.currentData() or "",
            "local_port": local_port,
            "remote_port": remote_port,
        }
        from config import ALIYUN_TTS_DEFAULTS
        aliyun_cfg = {**ALIYUN_TTS_DEFAULTS, **(config.get("aliyun_tts") or {})}
        voice_id = self.aliyun_voice_id.text().strip()
        aliyun_cfg.update({
            "api_key": self.aliyun_api_key.text().strip(),
            "voice_id": voice_id,
            "voice_cloned": bool(voice_id),
            "preferred_name": self.aliyun_preferred_name.text().strip() or "amadeus_kurisu",
            "engine": str(self.aliyun_engine.currentData() or ALIYUN_TTS_DEFAULTS["engine"]),
            # model 字段保留 aliyun_cfg 中的默认值（仅 qwen3-tts-vc 克隆路径用），
            # 不再暴露为 UI 输入（与 amadeus src/components/Settings.tsx 对齐）
        })
        config["aliyun_tts"] = aliyun_cfg
        # 关闭测试隧道（避免留下孤儿进程）
        if self._tunnel is not None:
            self._tunnel.stop()
            self._tunnel = None
        save_config(config)
        self.accept()
