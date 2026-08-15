# tests/test_openclaw_tool.py
"""operate_gui 工具测试：未启用降级 + Gateway 正常路径 + 不可达降级。适配 _operate_gui 实现。"""
from unittest.mock import MagicMock, patch


def test_operate_gui_disabled_returns_hint():
    from core.desktop_tools import execute_tool
    with patch("core.desktop_tools.OPENCLAW_DEFAULTS", {"enabled": False}):
        result = execute_tool("operate_gui", {"task": "打开记事本写一句话"})
    assert "未启用" in result["text"]


def test_operate_gui_empty_task():
    from core.desktop_tools import execute_tool
    with patch("core.desktop_tools.OPENCLAW_DEFAULTS", {"enabled": True}):
        result = execute_tool("operate_gui", {"task": "   "})
    assert result["text"] == "Empty GUI task."


def test_operate_gui_runs_when_gateway_available():
    from core.desktop_tools import execute_tool
    fake_resp = MagicMock()
    fake_resp.is_error = False
    fake_resp.json.return_value = {
        "choices": [{"message": {"content": "已完成：打开记事本并输入了一句话"}}]
    }
    with patch("core.desktop_tools.OPENCLAW_DEFAULTS",
               {"enabled": True, "base_url": "http://127.0.0.1:18789", "token": "", "model": "openclaw/default", "timeout": 5}), \
         patch("core.desktop_tools.httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.return_value = fake_resp
        result = execute_tool("operate_gui", {"task": "打开记事本写一句话"})
    assert "已完成" in result["text"]
    # 请求发往 Gateway 的 OpenAI 兼容端点
    _, kwargs = mock_client.return_value.__enter__.return_value.post.call_args
    assert kwargs["json"]["model"] == "openclaw/default"
    assert kwargs["json"]["messages"][0]["content"] == "打开记事本写一句话"


def test_operate_gui_gateway_unreachable():
    import httpx
    from core.desktop_tools import execute_tool
    with patch("core.desktop_tools.OPENCLAW_DEFAULTS",
               {"enabled": True, "base_url": "http://127.0.0.1:18789", "timeout": 1}), \
         patch("core.desktop_tools.httpx.Client", side_effect=httpx.ConnectError("refused")):
        result = execute_tool("operate_gui", {"task": "复杂任务"})
    assert "不可达" in result["text"]


def test_operate_gui_in_confirmation_required():
    from core.desktop_tools import CONFIRMATION_REQUIRED
    assert "operate_gui" in CONFIRMATION_REQUIRED
