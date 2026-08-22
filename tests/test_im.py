"""core/im 单元测试：OneBot 事件解析、CQ 码剥离、过滤/去重/免打扰。"""
from __future__ import annotations

from datetime import datetime

from core.im.filter import MessageFilter, _in_quiet_hours
from core.im.models import IMMessage, parse_onebot_event, strip_cq


# === strip_cq ===
def test_strip_cq_replaces_media_codes():
    assert strip_cq("看这个 [CQ:image,file=x.jpg] 好笑吗") == "看这个 [图片] 好笑吗"
    assert "[CQ:record,url=...]" .replace("[CQ:record,url=...]", "[语音]") == "[语音]"
    assert strip_cq("[CQ:at,qq=12345] 早") == "@12345 早"


# === parse_onebot_event ===
def _private_event(**over):
    ev = {
        "post_type": "message", "message_type": "private", "message_id": 1,
        "user_id": 100, "self_id": 999, "time": 1700000000,
        "message": "今晚吃饭吗", "raw_message": "今晚吃饭吗",
        "sender": {"user_id": 100, "nickname": "张三"},
    }
    ev.update(over)
    return ev


def test_parse_private_text():
    msg = parse_onebot_event(_private_event())
    assert msg is not None and msg.platform == "qq"
    assert msg.msg_type == "private" and msg.peer_id == "100"
    assert msg.sender_name == "张三" and msg.content == "今晚吃饭吗"
    assert "【QQ·私聊】张三：今晚吃饭吗" == msg.display()


def test_parse_ignores_non_message_events():
    assert parse_onebot_event({"post_type": "meta_event", "meta_event_type": "heartbeat"}) is None
    assert parse_onebot_event({"status": "ok", "retcode": 0, "echo": 1}) is None


def test_parse_group_array_segments_at_me():
    ev = _private_event(
        message_type="group", group_id=42,
        message=[{"type": "at", "data": {"qq": "999"}},
                 {"type": "text", "data": {"text": " 在吗"}},
                 {"type": "image", "data": {"file": "a.jpg"}}],
        sender={"user_id": 100, "card": "群名片", "nickname": "张三"},
    )
    msg = parse_onebot_event(ev)
    assert msg.msg_type == "group" and msg.peer_id == "42"
    assert msg.is_at_me and msg.sender_name == "群名片"
    assert msg.content == "@999 在吗[图片]"


def test_parse_group_at_other_not_at_me():
    ev = _private_event(message_type="group", group_id=42,
                        message=[{"type": "at", "data": {"qq": "111"}},
                                 {"type": "text", "data": {"text": "你好"}}])
    msg = parse_onebot_event(ev)
    assert msg is not None and not msg.is_at_me


def test_parse_cq_string_at_me():
    ev = _private_event(message_type="group", group_id=42,
                        message="[CQ:at,qq=999] 出来")
    msg = parse_onebot_event(ev)
    assert msg.is_at_me and msg.content == "@999 出来"


# === filter ===
def _mk(msg_id="1", msg_type="private", at=False, content="hi"):
    return IMMessage(platform="qq", msg_type=msg_type, peer_id="1", sender_name="s",
                     content=content, is_at_me=at, timestamp=1.0, message_id=msg_id)


CFG = {"qq": {"enabled": True, "group_at_only": True, "keywords": ["紧急"]},
       "notify": {"bubble": True, "tray": True, "tts": False},
       "quiet_hours": {"start": "23:00", "end": "08:00"}}


def test_filter_group_at_only(monkeypatch):
    f = MessageFilter(dict(CFG))
    monkeypatch.setattr(f, "in_quiet_hours", lambda: False)  # 固定不在免打扰时段
    assert not f.should_notify(_mk(msg_type="group", at=False))
    assert f.should_notify(_mk(msg_type="group", at=True))
    assert f.should_notify(_mk(msg_type="group", at=False, content="紧急：服务器挂了"))
    assert f.should_notify(_mk(msg_type="private"))


def test_filter_quiet_hours_suppresses(monkeypatch):
    f = MessageFilter(dict(CFG))
    monkeypatch.setattr(f, "in_quiet_hours", lambda: True)
    assert not f.should_notify(_mk(msg_type="private"))
    assert not f.should_notify(_mk(msg_type="group", at=True))


def test_filter_dedup():
    f = MessageFilter(dict(CFG))
    assert not f.is_duplicate(_mk(msg_id="a"))
    assert f.is_duplicate(_mk(msg_id="a"))
    assert not f.is_duplicate(_mk(msg_id="b"))


def test_quiet_hours_wraparound():
    assert _in_quiet_hours(datetime(2026, 1, 1, 23, 30), "23:00", "08:00")
    assert _in_quiet_hours(datetime(2026, 1, 1, 3, 0), "23:00", "08:00")
    assert not _in_quiet_hours(datetime(2026, 1, 1, 12, 0), "23:00", "08:00")
    assert not _in_quiet_hours(datetime(2026, 1, 1, 12, 0), "12:00", "12:00")
