# tests/test_agent_router_config.py
"""AGENT_ROUTER_DEFAULTS 结构与合法值约束。"""
from config import AGENT_ROUTER_DEFAULTS


def test_defaults_shape():
    assert AGENT_ROUTER_DEFAULTS["mode"] == "chat"
    assert set(AGENT_ROUTER_DEFAULTS) == {"mode", "codex"}


def test_codex_defaults():
    codex = AGENT_ROUTER_DEFAULTS["codex"]
    assert codex["sandbox"] in ("read-only", "workspace-write")
    assert isinstance(codex["timeout"], int) and codex["timeout"] > 0
    assert isinstance(codex["workspace"], str) and codex["workspace"]
