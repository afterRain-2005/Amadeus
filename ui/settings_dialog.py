"""Application settings with model, voice and input tabs."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
    QTabWidget, QVBoxLayout, QWidget,
)

from core.storage import load_config, save_config


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Amadeus 设置")
        self.setMinimumSize(520, 360)
        config = load_config()
        tabs = QTabWidget()

        model_page = QWidget()
        model_form = QFormLayout(model_page)
        self.endpoint = QLineEdit(config.get("endpoint", "https://api.deepseek.com/v1"))
        self.api_key = QLineEdit(config.get("api_key", ""))
        self.api_key.setEchoMode(QLineEdit.Password)
        self.model = QLineEdit(config.get("model", "deepseek-chat"))
        model_form.addRow("API Endpoint", self.endpoint)
        model_form.addRow("API Key", self.api_key)
        model_form.addRow("模型", self.model)
        tabs.addTab(model_page, "Chat 模型")

        voice_page = QWidget()
        voice_form = QFormLayout(voice_page)
        self.tts_enabled = QCheckBox("回复后自动朗读日语")
        self.tts_enabled.setChecked(config.get("tts_enabled", True))
        self.tts_rate = QComboBox()
        self.tts_rate.addItems(["慢", "正常", "快"])
        self.tts_rate.setCurrentIndex(config.get("tts_rate", 1))
        voice_form.addRow(self.tts_enabled)
        voice_form.addRow("语速", self.tts_rate)
        tabs.addTab(voice_page, "语音合成")

        asr_page = QWidget()
        asr_form = QFormLayout(asr_page)
        self.asr_endpoint = QLineEdit(config.get("asr_endpoint", "https://api.xiaomimimo.com/v1"))
        self.asr_key = QLineEdit(config.get("asr_api_key", ""))
        self.asr_key.setEchoMode(QLineEdit.Password)
        self.asr_model = QLineEdit(config.get("asr_model", "mimo-v2.5-asr"))
        asr_form.addRow("ASR Endpoint", self.asr_endpoint)
        asr_form.addRow("ASR API Key", self.asr_key)
        asr_form.addRow("ASR 模型", self.asr_model)
        tabs.addTab(asr_page, "语音输入")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    def _save(self) -> None:
        config = load_config()
        config.update({
            "endpoint": self.endpoint.text().strip(), "api_key": self.api_key.text().strip(),
            "model": self.model.text().strip(), "tts_enabled": self.tts_enabled.isChecked(),
            "tts_rate": self.tts_rate.currentIndex(), "asr_endpoint": self.asr_endpoint.text().strip(),
            "asr_api_key": self.asr_key.text().strip(), "asr_model": self.asr_model.text().strip(),
        })
        save_config(config)
        self.accept()
