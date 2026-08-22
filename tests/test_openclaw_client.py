# tests/test_openclaw_client.py
"""openclaw_client：配置合并 / 探活 / 拉起 / 流式对话 / CUA 任务（mock httpx + Popen，不打真网关）。"""
import json
from unittest.mock import MagicMock

import httpx
import pytest

from core import openclaw_client
from core.openclaw_client import (
    ensure_gateway,
    merge_config,
    probe_gateway,
    run_gui_task,
    run_openclaw_turn,
)


# === merge_config ===

def test_merge_config_defaults_when_missing():
    assert merge_config({})["base_url"] == "http://127.0.0.1:18789"
    assert merge_config({"openclaw": None})["enabled"] is False


def test_merge_config_runtime_override():
    cfg = merge_config({"openclaw": {"enabled": True, "token": "tok", "autostart": False}})
    assert cfg["enabled"] is True
    assert cfg["token"] == "tok"
    assert cfg["autostart"] is False
    assert cfg["model"] == "openclaw/default"  # 未覆盖项保留默认


def test_merge_config_reads_storage_when_no_arg(monkeypatch):
    monkeypatch.setattr("core.storage.load_config", lambda: {"openclaw": {"enabled": True}})
    assert merge_config()["enabled"] is True


# === probe_gateway ===

def test_probe_gateway_ok(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value.get.return_value = MagicMock(status_code=200)
    monkeypatch.setattr(openclaw_client.httpx, "Client", lambda **kw: client)
    assert probe_gateway("http://127.0.0.1:18789", "tok") is True


def test_probe_gateway_unauthorized(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value.get.return_value = MagicMock(status_code=401)
    monkeypatch.setattr(openclaw_client.httpx, "Client", lambda **kw: client)
    assert probe_gateway("http://127.0.0.1:18789", "bad") is False


def test_probe_gateway_conn_error(monkeypatch):
    def boom(**kw):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(openclaw_client.httpx, "Client", boom)
    assert probe_gateway("http://127.0.0.1:18789") is False


# === ensure_gateway（DI，同 test_hermes_launcher 风格） ===

def test_ensure_gateway_already_up():
    probe = MagicMock(return_value=True)
    popen = MagicMock()
    assert ensure_gateway(base_url="http://x", probe=probe, popen=popen) is True
    popen.assert_not_called()


def test_ensure_gateway_no_autostart_skips_popen():
    probe = MagicMock(return_value=False)
    popen = MagicMock()
    ok = ensure_gateway(base_url="http://x", autostart=False, probe=probe, popen=popen)
    assert ok is False
    popen.assert_not_called()


def test_ensure_gateway_starts_and_waits(monkeypatch):
    probe = MagicMock(side_effect=[False, False, True])
    popen = MagicMock()
    monkeypatch.setattr(openclaw_client.time, "sleep", lambda s: None)
    ok = ensure_gateway(
        base_url="http://127.0.0.1:18789", probe=probe, popen=popen, wait_timeout=30)
    assert ok is True
    assert popen.call_count == 1
    argv = popen.call_args.args[0]
    assert argv[:3] == ["openclaw", "gateway", "--port"]
    assert argv[3] == "18789"


def test_ensure_gateway_port_from_base_url(monkeypatch):
    probe = MagicMock(side_effect=[False, True])
    popen = MagicMock()
    monkeypatch.setattr(openclaw_client.time, "sleep", lambda s: None)
    ensure_gateway(base_url="http://127.0.0.1:19000", probe=probe, popen=popen)
    assert popen.call_args.args[0][3] == "19000"


def test_ensure_gateway_timeout(monkeypatch):
    probe = MagicMock(return_value=False)
    popen = MagicMock()
    monkeypatch.setattr(openclaw_client.time, "sleep", lambda s: None)
    ok = ensure_gateway(base_url="http://x", probe=probe, popen=popen, wait_timeout=3)
    assert ok is False


def test_ensure_gateway_popen_error_returns_false(tmp_path):
    probe = MagicMock(return_value=False)

    def boom(*args, **kwargs):
        raise OSError("openclaw not on PATH")

    ok = ensure_gateway(
        base_url="http://x", probe=probe, popen=boom,
        log_path="openclaw_test.log", wait_timeout=3,
    )
    assert ok is False


# === run_openclaw_turn（SSE 流式 + 非流回退 + 错误） ===

def _sse_response(*deltas: str) -> MagicMock:
    resp = MagicMock()
    resp.is_error = False
    resp.headers = {"content-type": "text/event-stream"}
    lines = []
    for d in deltas:
        lines.append("data: " + json.dumps({"choices": [{"delta": {"content": d}}]}))
    lines.append("data: [DONE]")
    resp.text = "\n".join(lines)
    return resp


def test_run_openclaw_turn_streams_deltas(monkeypatch):
    resp = _sse_response("你", "好", "呀")
    client = MagicMock()
    client.__enter__.return_value.post.return_value = resp
    monkeypatch.setattr(openclaw_client.httpx, "Client", lambda **kw: client)
    got: list[str] = []
    reply = run_openclaw_turn(
        base_url="http://127.0.0.1:18789", token="tok", model="openclaw/default",
        soul_md="SOUL", instructions="FMT", input_text="你好",
        on_delta=got.append,
    )
    assert reply == "你好呀"
    assert got == ["你", "好", "呀"]
    # 请求结构：system(soul+格式+记忆) + 历史 + user
    kwargs = client.__enter__.return_value.post.call_args.kwargs
    messages = kwargs["json"]["messages"]
    assert messages[0]["role"] == "system"
    assert "SOUL" in messages[0]["content"] and "FMT" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "你好"}
    assert kwargs["json"]["model"] == "openclaw/default"
    assert kwargs["json"]["stream"] is True
    assert kwargs["headers"]["Authorization"] == "Bearer tok"


def test_run_openclaw_turn_includes_history_and_memories(monkeypatch):
    resp = _sse_response("ok")
    client = MagicMock()
    client.__enter__.return_value.post.return_value = resp
    monkeypatch.setattr(openclaw_client.httpx, "Client", lambda **kw: client)
    run_openclaw_turn(
        base_url="http://x", token="", model="m",
        soul_md="S", instructions="I", input_text="q",
        conversation_history=[{"role": "user", "content": "old"}],
        memories=[{"content": "用户喜欢咖啡"}],
    )
    messages = client.__enter__.return_value.post.call_args.kwargs["json"]["messages"]
    assert messages[1] == {"role": "user", "content": "old"}
    assert "用户喜欢咖啡" in messages[0]["content"]


def test_run_openclaw_turn_non_sse_fallback(monkeypatch):
    resp = MagicMock()
    resp.is_error = False
    resp.headers = {"content-type": "application/json"}
    resp.json.return_value = {"choices": [{"message": {"content": "完整回复"}}]}
    client = MagicMock()
    client.__enter__.return_value.post.return_value = resp
    monkeypatch.setattr(openclaw_client.httpx, "Client", lambda **kw: client)
    got: list[str] = []
    reply = run_openclaw_turn(
        base_url="http://x", token="", model="m",
        soul_md="S", instructions="I", input_text="q", on_delta=got.append,
    )
    assert reply == "完整回复"
    assert got == ["完整回复"]


def test_run_openclaw_turn_http_error(monkeypatch):
    resp = MagicMock()
    resp.is_error = True
    resp.status_code = 500
    resp.text = "boom"
    client = MagicMock()
    client.__enter__.return_value.post.return_value = resp
    monkeypatch.setattr(openclaw_client.httpx, "Client", lambda **kw: client)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        run_openclaw_turn(
            base_url="http://x", token="", model="m",
            soul_md="S", instructions="I", input_text="q")


def test_run_openclaw_turn_conn_error(monkeypatch):
    def boom(**kw):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(openclaw_client.httpx, "Client", boom)
    with pytest.raises(RuntimeError, match="请求失败"):
        run_openclaw_turn(
            base_url="http://x", token="", model="m",
            soul_md="S", instructions="I", input_text="q")


# === run_gui_task（CUA 委托 + 降级链） ===

def _cfg(**kw) -> dict:
    base = {
        "enabled": True, "base_url": "http://127.0.0.1:18789",
        "token": "tok", "model": "openclaw/default", "timeout": 5,
        "autostart": False,
    }
    base.update(kw)
    return base


def test_run_gui_task_disabled_hint():
    assert "未启用" in run_gui_task(_cfg(enabled=False), "打开记事本")


def test_run_gui_task_gateway_down(monkeypatch):
    monkeypatch.setattr(openclaw_client, "probe_gateway", lambda *a, **kw: False)
    assert "不可达" in run_gui_task(_cfg(), "打开记事本")


def test_run_gui_task_ok(monkeypatch):
    monkeypatch.setattr(openclaw_client, "probe_gateway", lambda *a, **kw: True)
    resp = _sse_response("已完成：打开记事本")
    client = MagicMock()
    client.__enter__.return_value.post.return_value = resp
    monkeypatch.setattr(openclaw_client.httpx, "Client", lambda **kw: client)
    statuses: list[str] = []
    reply = run_gui_task(_cfg(), "打开记事本", on_status=statuses.append)
    assert "已完成" in reply
    assert any("OpenClaw" in s for s in statuses)
    kwargs = client.__enter__.return_value.post.call_args.kwargs
    assert kwargs["json"]["messages"] == [{"role": "user", "content": "打开记事本"}]


def test_run_gui_task_empty_reply(monkeypatch):
    monkeypatch.setattr(openclaw_client, "probe_gateway", lambda *a, **kw: True)
    resp = _sse_response("")
    client = MagicMock()
    client.__enter__.return_value.post.return_value = resp
    monkeypatch.setattr(openclaw_client.httpx, "Client", lambda **kw: client)
    assert "空回复" in run_gui_task(_cfg(), "任务")


def test_run_gui_task_autostart_ensures_gateway(monkeypatch):
    ensure_calls: list[dict] = []

    def fake_ensure(**kw):
        ensure_calls.append(kw)
        return True

    monkeypatch.setattr(openclaw_client, "ensure_gateway", fake_ensure)
    resp = _sse_response("done")
    client = MagicMock()
    client.__enter__.return_value.post.return_value = resp
    monkeypatch.setattr(openclaw_client.httpx, "Client", lambda **kw: client)
    reply = run_gui_task(_cfg(autostart=True), "任务")
    assert "done" in reply
    assert ensure_calls and ensure_calls[0]["autostart"] is True
    assert ensure_calls[0]["base_url"] == "http://127.0.0.1:18789"
