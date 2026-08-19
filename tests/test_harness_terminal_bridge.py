from __future__ import annotations

from types import SimpleNamespace


def _notification(event_type: str, data: dict):
    return SimpleNamespace(
        method="session.event",
        payload={"event": {"type": event_type, "data": data}},
    )


def test_event_adapter_streams_chunks_without_replaying_final_message():
    from core.harness_bridge import _HarnessEventAdapter

    deltas: list[str] = []
    adapter = _HarnessEventAdapter(
        on_delta=deltas.append,
        on_status=lambda _text: None,
        on_tool_event=lambda _event: None,
    )
    adapter(_notification("assistant/chunk", {
        "turn": 1,
        "step": 1,
        "chunk": {"type": "text-delta", "text": "流式"},
    }))
    adapter(_notification("assistant/message", {
        "turn": 1,
        "step": 1,
        "message": {"content": [{"type": "text", "text": "流式"}]},
    }))

    assert deltas == ["流式"]


def test_event_adapter_pairs_real_tool_result_schema():
    from core.harness_bridge import _HarnessEventAdapter

    events: list[dict] = []
    statuses: list[str] = []
    adapter = _HarnessEventAdapter(
        on_delta=lambda _text: None,
        on_status=statuses.append,
        on_tool_event=events.append,
    )
    adapter(_notification("tool/call", {
        "callId": "call-1",
        "name": "bash",
        "arguments": '{"command":"Get-ChildItem"}',
    }))
    adapter(_notification("tool/result", {
        "message": {
            "source": {"kind": "tool", "callId": "call-1"},
            "content": [{
                "type": "tool-result",
                "toolCallId": "call-1",
                "content": [{"type": "text", "text": "a.txt"}],
                "isError": False,
            }],
        },
    }))

    assert events[-1] == {
        "kind": "tool_result",
        "callId": "call-1",
        "name": "bash",
        "arguments": {"command": "Get-ChildItem"},
        "content": "a.txt",
        "isError": False,
    }
    assert statuses[-1] == "工具执行完成"


def test_event_adapter_surfaces_tool_error():
    from core.harness_bridge import _HarnessEventAdapter

    events: list[dict] = []
    adapter = _HarnessEventAdapter(
        on_delta=lambda _text: None,
        on_status=lambda _text: None,
        on_tool_event=events.append,
    )
    adapter(_notification("tool/call", {
        "callId": "call-2",
        "name": "str_replace_editor",
        "arguments": {"command": "create", "path": "x.py", "file_text": "x"},
    }))
    adapter(_notification("tool/result", {
        "error": {"name": "ToolError", "code": "EACCES"},
        "message": {
            "source": {"kind": "tool", "callId": "call-2"},
            "content": [{
                "type": "tool-result",
                "toolCallId": "call-2",
                "content": [],
                "isError": True,
            }],
        },
    }))

    assert events[-1]["isError"] is True
    assert events[-1]["content"] == "ToolError: EACCES"
    assert events[-1]["arguments"]["path"] == "x.py"
