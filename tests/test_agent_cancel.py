"""Ctrl+C 打断模型：cancel_event 从 route_and_send 到 HTTP 流式循环的链路。"""
import json
import threading

import core.llm.agent_client as ac
from core.llm import backend_router
from core.llm.agent_client import _stream_turn_direct, run_local_run


class _FakeResponse:
    """假 httpx 流式响应：iter_lines 逐行吐 SSE data 帧。"""

    is_error = False

    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self):
        yield from self._lines


class _FakeClient:
    def __init__(self, response):
        self._response = response

    def stream(self, *args, **kwargs):
        return self._response


def _sse(content: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": content}}]})


def test_stream_turn_direct_cancel_keeps_partial(monkeypatch):
    """流中置位 cancel_event：立即停止消费，返回已生成正文，丢弃半途工具调用。"""
    cancel = threading.Event()
    seen = []

    def on_delta(text):
        seen.append(text)
        cancel.set()  # 第一个 delta 后模拟用户按下 Ctrl+C

    lines = [_sse("你好"), _sse("，世界"), _sse("！")]
    monkeypatch.setattr(ac, "_get_direct_client", lambda: _FakeClient(_FakeResponse(lines)))
    content, calls = _stream_turn_direct(
        "http://x/v1", {}, "m", [], on_delta, cancel_event=cancel
    )
    assert content == "你好"
    assert calls == []
    assert seen == ["你好"]


def test_stream_turn_direct_no_cancel_consumes_all(monkeypatch):
    """未置位时行为不变：完整消费流。"""
    lines = [_sse("你好"), _sse("，世界"), "data: [DONE]"]
    monkeypatch.setattr(ac, "_get_direct_client", lambda: _FakeClient(_FakeResponse(lines)))
    content, calls = _stream_turn_direct(
        "http://x/v1", {}, "m", [], lambda _: None, cancel_event=threading.Event()
    )
    assert content == "你好，世界"
    assert calls == []


def test_run_local_run_cancel_skips_tool_execution(monkeypatch):
    """回合产出工具调用后置位中断：不执行工具，直接返回部分回复。"""
    cancel = threading.Event()
    tool_calls = [
        {"id": "1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}
    ]

    def fake_stream(url, headers, model, messages, on_delta, max_tokens=700, cancel_event=None):
        cancel.set()  # 流刚结束就被打断
        return "部分回复", tool_calls

    executed = []

    def fake_execute(name, arguments, vision_capable):
        executed.append(name)
        return {"text": "ok"}

    monkeypatch.setattr(ac, "_stream_turn_direct", fake_stream)
    monkeypatch.setattr(ac, "_execute_tool_safe", fake_execute)
    reply = run_local_run(
        endpoint="http://x/v1", api_key="k", model="m",
        soul_md="s", instructions="i", input_text="hi",
        cancel_event=cancel,
    )
    assert reply == "部分回复"
    assert executed == []


def test_route_and_send_forwards_cancel_event(monkeypatch):
    """route_and_send 把 cancel_event 原样传给本地直连后端。"""
    captured = {}

    def fake_run(**kw):
        captured.update(kw)
        return "回复"

    monkeypatch.setattr(ac, "run_local_run", fake_run)
    cancel = threading.Event()
    reply, backend = backend_router.route_and_send(
        config={"agent_router": {"mode": "chat"},
                "endpoint": "http://x", "api_key": "k", "model": "m"},
        input_text="hi", soul_md="soul", cancel_event=cancel,
    )
    assert (reply, backend) == ("回复", "chat")
    assert captured["cancel_event"] is cancel
