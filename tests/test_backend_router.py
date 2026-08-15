# tests/test_backend_router.py
"""backend_router：分类矩阵 + 分发/降级链（monkeypatch 后端函数，不打真 API）。"""
import pytest

from core import backend_router
from core.backend_router import classify_input


@pytest.mark.parametrize("text,expected", [
    ("你好", "chat"),
    ("晚上好呀", "chat"),
    ("帮我搜一下今天天气", "agent"),
    ("读一下 D 盘的文件", "agent"),
    ("打开记事本", "gui"),
    ("截个屏", "gui"),
])
def test_classify_rules(text, expected):
    assert classify_input(text, openclaw_enabled=True) == expected


def test_classify_gui_without_openclaw():
    # openclaw 未启用时 gui 意图不成立，落到 agent/chat
    assert classify_input("打开记事本", openclaw_enabled=False) in ("agent", "chat")


def test_classify_llm_injectable():
    assert classify_input("一段模糊的话", llm_classify=lambda t: "agent") == "agent"


def test_classify_llm_exception_defaults_chat():
    def boom(t):
        raise RuntimeError("net down")
    assert classify_input("一段模糊的话", llm_classify=boom) == "chat"


def test_classify_llm_invalid_value_defaults_chat():
    assert classify_input("一段模糊的话", llm_classify=lambda t: "乱值") == "chat"


def _cfg(mode, **kw):
    return {"agent_router": {"mode": mode, **kw},
            "endpoint": "http://x", "api_key": "k", "model": "m"}


def test_route_chat_uses_local(monkeypatch):
    import core.agent_client as ac
    monkeypatch.setattr(ac, "run_local_run", lambda **kw: "本地回复")
    reply, backend = backend_router.route_and_send(
        config=_cfg("chat"), input_text="你好", soul_md="soul")
    assert (reply, backend) == ("本地回复", "chat")


def test_route_hermes_ok(monkeypatch):
    import core.agent_client as ac
    import core.hermes_launcher as hl
    monkeypatch.setattr(hl, "read_profile_api_key", lambda p: "hk")
    monkeypatch.setattr(hl, "ensure_gateway", lambda **kw: True)
    monkeypatch.setattr(ac, "run_hermes_run", lambda **kw: "hermes 回复")
    reply, backend = backend_router.route_and_send(
        config=_cfg("hermes"), input_text="hi", soul_md="soul")
    assert (reply, backend) == ("hermes 回复", "hermes")


def test_route_hermes_gateway_down_fallback(monkeypatch):
    import core.agent_client as ac
    import core.hermes_launcher as hl
    monkeypatch.setattr(hl, "read_profile_api_key", lambda p: "hk")
    monkeypatch.setattr(hl, "ensure_gateway", lambda **kw: False)
    monkeypatch.setattr(ac, "run_local_run", lambda **kw: "本地回复")
    statuses = []
    reply, backend = backend_router.route_and_send(
        config=_cfg("hermes"), input_text="hi", soul_md="soul",
        on_status=statuses.append)
    assert backend == "chat"
    assert any("本地直连" in s for s in statuses)


def test_route_hermes_runerror_fallback(monkeypatch):
    import core.agent_client as ac
    import core.hermes_launcher as hl
    monkeypatch.setattr(hl, "read_profile_api_key", lambda p: "hk")
    monkeypatch.setattr(hl, "ensure_gateway", lambda **kw: True)

    def boom(**kw):
        raise RuntimeError("run.failed")

    monkeypatch.setattr(ac, "run_hermes_run", boom)
    monkeypatch.setattr(ac, "run_local_run", lambda **kw: "本地回复")
    reply, backend = backend_router.route_and_send(
        config=_cfg("hermes"), input_text="hi", soul_md="soul")
    assert (reply, backend) == ("本地回复", "chat")


def test_route_codex(monkeypatch, tmp_path):
    import core.codex_client as cc
    monkeypatch.setattr(backend_router, "_codex_session_started", False)
    monkeypatch.setattr(cc, "ensure_agents_md", lambda ws, s, o: ws / "AGENTS.md")
    monkeypatch.setattr(cc, "run_codex_turn", lambda **kw: "codex 回复")
    cfg = _cfg("codex", codex={"workspace": str(tmp_path)})
    reply, backend = backend_router.route_and_send(
        config=cfg, input_text="hi", soul_md="soul")
    assert (reply, backend) == ("codex 回复", "codex")
    assert backend_router._codex_session_started is True


def test_route_auto_uses_classify(monkeypatch):
    import core.agent_client as ac
    monkeypatch.setattr(ac, "run_local_run", lambda **kw: "ok")
    monkeypatch.setattr(backend_router, "classify_input", lambda text, **kw: "chat")
    reply, backend = backend_router.route_and_send(
        config=_cfg("auto"), input_text="hi", soul_md="soul")
    assert backend == "chat"


def test_route_gui_nudge_local(monkeypatch):
    import core.agent_client as ac
    seen = {}

    def fake_local(**kw):
        seen.update(kw)
        return "ok"

    monkeypatch.setattr(ac, "run_local_run", fake_local)
    monkeypatch.setattr(backend_router, "classify_input", lambda text, **kw: "gui")
    cfg = _cfg("auto")
    cfg["openclaw"] = {"enabled": True}
    reply, backend = backend_router.route_and_send(
        config=cfg, input_text="打开记事本", soul_md="soul")
    assert backend == "gui"
    assert "operate_gui" in seen["input_text"]
