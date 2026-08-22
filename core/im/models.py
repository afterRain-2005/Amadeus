"""统一 IM 消息模型 + OneBot 11 事件解析。

上层（过滤、通知、记忆）只认 IMMessage，不感知来源平台。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IMMessage:
    platform: str          # "qq" | "wechat"
    msg_type: str          # "private" | "group"
    peer_id: str           # 私聊=对方 QQ；群聊=群号
    sender_name: str
    content: str           # 已剥离 CQ 码的纯文本
    is_at_me: bool
    timestamp: float
    message_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def display(self) -> str:
        """通知用一行摘要：'【QQ·私聊】张三：今晚吃饭吗'。"""
        kind = "私聊" if self.msg_type == "private" else f"群 {self.peer_id}"
        return f"【{self.platform.upper()}·{kind}】{self.sender_name}：{self.content}"


# === CQ 码 → 可读文本 ===
_CQ_PLACEHOLDER = {
    "image": "[图片]",
    "record": "[语音]",
    "video": "[视频]",
    "face": "[表情]",
    "bface": "[表情]",
    "mface": "[卡片]",
    "reply": "[回复]",
    "json": "[卡片]",
    "xml": "[卡片]",
    "file": "[文件]",
}
_CQ_RE = re.compile(r"\[CQ:(\w+)((?:,[^\]]*)?)\]")


def strip_cq(text: str) -> str:
    """把 CQ 码替换为可读占位（[图片] 等），@码转为 @昵称 形式。"""
    def _sub(m: re.Match) -> str:
        cmd, args = m.group(1), m.group(2)
        if cmd == "at":
            qq = ""
            for kv in args.strip(",").split(","):
                if kv.startswith("qq="):
                    qq = kv[3:]
            return f"@{qq}" if qq else "@某人"
        return _CQ_PLACEHOLDER.get(cmd, f"[{cmd}]")
    return _CQ_RE.sub(_sub, text).strip()


def _message_to_segments(message: Any) -> list[dict[str, Any]]:
    """OneBot message 字段兼容 string（CQ 码）与 array（消息段）两种格式。

    string 用 _CQ_RE 切成 text/at 段（保证 @机器人 可检测）；array 原样返回。
    """
    if isinstance(message, str):
        segments: list[dict[str, Any]] = []
        pos = 0
        for m in _CQ_RE.finditer(message):
            if m.start() > pos:
                segments.append({"type": "text", "data": {"text": message[pos:m.start()]}})
            args = m.group(2).strip(",")
            if m.group(1) == "at":
                data: dict[str, Any] = {}
                for kv in args.split(","):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        data[k] = v
                segments.append({"type": "at", "data": data})
            else:
                segments.append({"type": m.group(1), "data": {}})
            pos = m.end()
        if pos < len(message):
            segments.append({"type": "text", "data": {"text": message[pos:]}})
        return segments
    if isinstance(message, list):
        return [s for s in message if isinstance(s, dict)]
    return []


def parse_onebot_event(event: dict[str, Any]) -> IMMessage | None:
    """把一条 OneBot 11 上报事件解析为 IMMessage；非消息事件返回 None。"""
    if event.get("post_type") != "message":
        return None
    self_id = str(event.get("self_id", ""))
    msg_type = event.get("message_type", "private")

    # 先检测 @机器人（在剥离 CQ 码之前），再拼接可读文本
    is_at_me = False
    texts: list[str] = []
    for seg in _message_to_segments(event.get("message") if event.get("message") is not None
                                    else event.get("raw_message")):
        seg_type = seg.get("type", "")
        data = seg.get("data") or {}
        if seg_type == "at":
            if str(data.get("qq", "")) == self_id:
                is_at_me = True
            qq = str(data.get("qq", ""))
            texts.append(f"@{qq}" if qq else "@某人")
        elif seg_type == "text":
            texts.append(str(data.get("text", "")))
        else:
            texts.append(_CQ_PLACEHOLDER.get(seg_type, f"[{seg_type}]"))

    content = strip_cq("".join(texts))
    sender = event.get("sender") or {}
    sender_name = str(sender.get("card") or sender.get("nickname") or sender.get("user_id") or "未知")

    return IMMessage(
        platform="qq",
        msg_type="group" if msg_type == "group" else "private",
        peer_id=str(event.get("group_id") or event.get("user_id") or ""),
        sender_name=sender_name,
        content=content,
        is_at_me=is_at_me,
        timestamp=float(event.get("time") or time.time()),
        message_id=str(event.get("message_id", "")),
        raw=event,
    )
