# tests/test_hermes_launcher.py
"""hermes_launcher：探活/拉起逻辑（mock httpx + Popen，不打真网关）。"""
from unittest.mock import MagicMock

import httpx

from core import hermes_launcher
from core.hermes_launcher import ensure_gateway, probe_health, read_profile_api_key


def test_read_profile_api_key(tmp_path, monkeypatch):
    profile_dir = tmp_path / ".hermes" / "profiles" / "kurisu"
    profile_dir.mkdir(parents=True)
    (profile_dir / ".env").write_text(
        "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=abc123\n", encoding="utf-8")
    monkeypatch.setattr(hermes_launcher.Path, "home", staticmethod(lambda: tmp_path))
    assert read_profile_api_key("kurisu") == "abc123"


def test_read_profile_api_key_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(hermes_launcher.Path, "home", staticmethod(lambda: tmp_path))
    assert read_profile_api_key("kurisu") is None


def test_probe_health_ok(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value.get.return_value = MagicMock(status_code=200)
    monkeypatch.setattr(hermes_launcher.httpx, "Client", lambda **kw: client)
    assert probe_health("http://127.0.0.1:8642", "k") is True


def test_probe_health_conn_error(monkeypatch):
    def boom(**kw):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(hermes_launcher.httpx, "Client", boom)
    assert probe_health("http://127.0.0.1:8642") is False


def test_ensure_gateway_already_up():
    probe = MagicMock(return_value=True)
    popen = MagicMock()
    assert ensure_gateway(base_url="http://x", api_key="k", probe=probe, popen=popen) is True
    popen.assert_not_called()


def test_ensure_gateway_starts_and_waits(monkeypatch):
    probe = MagicMock(side_effect=[False, False, True])
    popen = MagicMock()
    monkeypatch.setattr(hermes_launcher.time, "sleep", lambda s: None)
    ok = ensure_gateway(base_url="http://x", probe=probe, popen=popen, wait_timeout=30)
    assert ok is True
    assert popen.call_count == 1
    argv = popen.call_args.args[0]
    assert argv[0] == "hermes" and argv[1] == "-p" and "gateway" in argv


def test_ensure_gateway_timeout(monkeypatch):
    probe = MagicMock(return_value=False)
    popen = MagicMock()
    monkeypatch.setattr(hermes_launcher.time, "sleep", lambda s: None)
    ok = ensure_gateway(base_url="http://x", probe=probe, popen=popen, wait_timeout=3)
    assert ok is False


def test_ensure_gateway_popen_error_returns_false(tmp_path):
    probe = MagicMock(return_value=False)

    def boom(*args, **kwargs):
        raise OSError("permission denied")

    ok = ensure_gateway(
        base_url="http://x",
        probe=probe,
        popen=boom,
        log_path=tmp_path / "hermes.log",
        wait_timeout=3,
    )
    assert ok is False
