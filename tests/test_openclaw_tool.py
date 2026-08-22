# tests/test_openclaw_tool.py
"""operate_gui 工具测试：空任务 + 运行时配置覆盖 + 降级链（未启用/网关不可达）+ on_status 透传。"""
import json
from unittest.mock import MagicMock

from core.desktop_tools import execute_tool


def _patch_runtime_openclaw(monkeypatch, cfg: dict):
    monkeypatch.setattr("core.storage.load_config", lambda: {"openclaw": cfg})


def _sse_resp(text: str) -> MagicMock:
    resp = MagicMock()
    resp.is_error = False
    resp.headers = {"content-type": "text/event-stream"}
    resp.text = "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\ndata: [DONE]"
    return resp


def test_operate_gui_empty_task():
    result = execute_tool("operate_gui", {"task": "   "})
    assert result["text"] == "Empty GUI task."


def test_operate_gui_disabled_returns_hint(monkeypatch):
    _patch_runtime_openclaw(monkeypatch, {"enabled": False})
    result = execute_tool("operate_gui", {"task": "打开记事本写一句话"})
    assert "未启用" in result["text"]


def test_operate_gui_runtime_config_override(monkeypatch):
    """data/config.json 的 openclaw 运行时覆盖生效（而非 config.py 静态默认值）。"""
    import core.openclaw_client as oc
    _patch_runtime_openclaw(monkeypatch, {
        "enabled": True, "base_url": "http://127.0.0.1:19999",
        "token": "rt-token", "model": "openclaw/custom", "timeout": 5,
        "autostart": False,
    })
    monkeypatch.setattr(oc, "probe_gateway", lambda *a, **kw: True)
    client = MagicMock()
    client.__enter__.return_value.post.return_value = _sse_resp("已完成：打开记事本")
    monkeypatch.setattr(oc.httpx, "Client", lambda **kw: client)
    result = execute_tool("operate_gui", {"task": "打开记事本写一句话"})
    assert "已完成" in result["text"]
    url = client.__enter__.return_value.post.call_args.args[0]
    kwargs = client.__enter__.return_value.post.call_args.kwargs
    assert url == "http://127.0.0.1:19999/v1/chat/completions"
    assert kwargs["json"]["model"] == "openclaw/custom"
    assert kwargs["headers"]["Authorization"] == "Bearer rt-token"


def test_operate_gui_gateway_unreachable(monkeypatch):
    import core.openclaw_client as oc
    _patch_runtime_openclaw(monkeypatch, {"enabled": True, "autostart": False})
    monkeypatch.setattr(oc, "probe_gateway", lambda *a, **kw: False)
    result = execute_tool("operate_gui", {"task": "复杂任务"})
    assert "不可达" in result["text"]


def test_operate_gui_status_passthrough(monkeypatch):
    import core.openclaw_client as oc
    _patch_runtime_openclaw(monkeypatch, {"enabled": True, "autostart": False})
    monkeypatch.setattr(oc, "probe_gateway", lambda *a, **kw: True)
    client = MagicMock()
    client.__enter__.return_value.post.return_value = _sse_resp("done")
    monkeypatch.setattr(oc.httpx, "Client", lambda **kw: client)
    statuses: list[str] = []
    execute_tool("operate_gui", {"task": "任务"}, on_status=statuses.append)
    assert any("OpenClaw" in s for s in statuses)


def test_operate_gui_in_confirmation_required():
    from core.desktop_tools import CONFIRMATION_REQUIRED
    assert "operate_gui" in CONFIRMATION_REQUIRED
