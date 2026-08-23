# tests/test_settings_about.py
"""设置页「关于」tab 与 version_check_url 持久化测试。"""
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_dialog(qapp):
    from ui.settings_dialog import SettingsDialog
    with patch("ui.settings_dialog.load_config", return_value={}), \
         patch("ui.settings_dialog.save_config") as mock_save:
        dlg = SettingsDialog()
    return dlg, mock_save


def test_about_tab_exists(qapp):
    dlg, _ = _make_dialog(qapp)
    from PySide6.QtWidgets import QTabWidget
    tabw = dlg.findChildren(QTabWidget)[0]
    names = [tabw.tabText(i) for i in range(tabw.count())]
    assert "关于" in names
    assert "直连模型（默认）" in names


def test_about_shows_current_version(qapp):
    from core.version import __version__
    dlg, _ = _make_dialog(qapp)
    # 版本行是 QFormLayout 的 label+field，直接查 version_status 与版本 label
    from PySide6.QtWidgets import QLabel
    labels = [l.text() for l in dlg.findChildren(QLabel)]
    assert __version__ in labels


def test_check_update_without_url(qapp):
    dlg, _ = _make_dialog(qapp)
    dlg.version_check_url.setText("")
    dlg._check_update()
    assert dlg.version_status.text() == "未配置 URL 或检查失败"


def test_check_update_newer_version(qapp):
    dlg, _ = _make_dialog(qapp)
    dlg.version_check_url.setText("https://example.com/v.txt")
    with patch("core.version.check_latest_version", return_value="9.9.9"):
        dlg._check_update()
    assert "9.9.9" in dlg.version_status.text()
    assert "有新版" in dlg.version_status.text()


def test_save_persists_version_check_url(qapp):
    dlg, _ = _make_dialog(qapp)
    dlg.version_check_url.setText("https://example.com/version.txt")
    # _save 全程 mock 掉 load/save，绝不落盘真实 config.json
    with patch("ui.settings_dialog.load_config", return_value={}), \
         patch("ui.settings_dialog.save_config") as mock_save:
        dlg._save()
    saved = mock_save.call_args[0][0]
    assert saved["version_check_url"] == "https://example.com/version.txt"
