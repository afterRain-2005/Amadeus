"""Harness session continuity at the terminal routing boundary."""
from __future__ import annotations


def test_terminal_harness_session_id_reaches_bridge(monkeypatch):
    import core.llm.backend_router as router
    import core.llm.harness_bridge as bridge

    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(bridge, "run_harness_turn", fake_run)
    config = {
        "agent_router": {"mode": "harness"},
        "harness": {"api_key": "key", "model": "deepseek-chat"},
        "endpoint": "http://fallback",
        "api_key": "fallback",
        "model": "fallback-model",
    }

    reply, backend = router.route_and_send(
        config=config,
        input_text="继续刚才的任务",
        soul_md="soul",
        harness_session_id="amadeus-terminal-stable",
    )

    assert (reply, backend) == ("ok", "harness")
    assert captured["session_id"] == "amadeus-terminal-stable"
