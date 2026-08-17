# tests/test_settings_agent_tab.py
"""设置页 Agent tab：加载默认值 + 保存写回（mock 完整罩住 load/save，防真实 config 被写）。"""
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication, QTabWidget


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_dialog(qapp, store):
    with patch("ui.settings_dialog.load_config", return_value=store), \
         patch("ui.settings_dialog.save_config"):
        from ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog()
    return dlg


def test_agent_tab_exists(qapp):
    dlg = _make_dialog(qapp, {})
    tabw = dlg.findChildren(QTabWidget)[0]
    names = [tabw.tabText(i) for i in range(tabw.count())]
    assert "Agent 模式" in names


def test_agent_tab_defaults(qapp):
    dlg = _make_dialog(qapp, {})
    assert dlg.agent_mode.currentData() == "chat"
    assert dlg.codex_sandbox.currentData() == "read-only"
    assert dlg.deepseek_base_url.text() == "http://127.0.0.1:8642"


def test_agent_tab_save(qapp):
    store = {}
    dlg = _make_dialog(qapp, store)
    dlg.agent_mode.setCurrentIndex(dlg.agent_mode.findData("auto"))  # auto
    dlg.codex_sandbox.setCurrentIndex(1)    # workspace-write
    dlg.hermes_key.setText("hk")
    dlg.deepseek_base_url.setText("http://harness")
    dlg.deepseek_api_key.setText("dk")
    dlg.deepseek_model.setText("v3")
    with patch("ui.settings_dialog.load_config", return_value=store), \
         patch("ui.settings_dialog.save_config") as save_mock:
        dlg._save()
    saved = save_mock.call_args.args[0]
    assert saved["agent_router"]["mode"] == "auto"
    assert saved["agent_router"]["codex"]["sandbox"] == "workspace-write"
    assert saved["hermes"]["api_key"] == "hk"
    assert saved["deepseek"] == {
        "base_url": "http://harness", "api_key": "dk", "model": "v3"
    }
