# tests/test_ipc_command.py
"""overlay→renderer 命令序列化与 JS 应用纯函数测试（duplex 管道 IPC）。"""
from core.ipc_command import serialize_command, apply_command_js


def test_serialize_command_emotion():
    assert serialize_command(emotion="smile") == ("command", {"emotion": "smile"})


def test_serialize_command_speaking():
    assert serialize_command(speaking=True) == ("command", {"speaking": True})


def test_serialize_command_multi():
    out = serialize_command(emotion="angry", speaking=False)
    assert out[0] == "command"
    assert out[1] == {"emotion": "angry", "speaking": False}


def test_apply_command_js_emotion():
    assert apply_command_js({"emotion": "blush"}) == "window.__amadeus.setEmotion('blush')"


def test_apply_command_js_speaking_true():
    assert apply_command_js({"speaking": True}) == "window.__amadeus.setSpeaking(true)"


def test_apply_command_js_speaking_false():
    assert apply_command_js({"speaking": False}) == "window.__amadeus.setSpeaking(false)"


def test_apply_command_js_both():
    js = apply_command_js({"emotion": "smile", "speaking": True})
    assert "setEmotion('smile')" in js
    assert "setSpeaking(true)" in js


def test_apply_command_js_empty():
    assert apply_command_js({}) == ""
