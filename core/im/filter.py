"""消息过滤：去重、群聊 @/关键词策略、免打扰时段、本地缓冲。

缓冲落盘 data/im_buffer.jsonl（滚动保留 7 天），供后续"刚才群里聊了什么"查询。
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict
from datetime import datetime, timedelta

from core.im.models import IMMessage
from core.storage import APP_DIR

_BUFFER_FILE = APP_DIR / "im_buffer.jsonl"
_BUFFER_DAYS = 7
_SEEN_MAX = 512


def _in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    """判断 now 是否落在 [start, end) 免打扰时段（支持跨零点，如 23:00-08:00）。"""
    def _to_min(hhmm: str, default: int) -> int:
        try:
            h, m = hhmm.strip().split(":")
            return int(h) * 60 + int(m)
        except (ValueError, AttributeError):
            return default

    cur = now.hour * 60 + now.minute
    s = _to_min(start, 23 * 60)
    e = _to_min(end, 8 * 60)
    if s == e:
        return False
    if s < e:
        return s <= cur < e
    return cur >= s or cur < e  # 跨零点


class MessageFilter:
    """should_notify() 决定是否通知；所有消息（含被过滤的）都写缓冲。"""

    def __init__(self, config: dict) -> None:
        """config 为合并默认值后的 im 配置（见 config.IM_DEFAULTS）。"""
        self.config = config
        self._seen: deque[str] = deque(maxlen=_SEEN_MAX)

    def is_duplicate(self, msg: IMMessage) -> bool:
        if not msg.message_id:
            return False
        if msg.message_id in self._seen:
            return True
        self._seen.append(msg.message_id)
        return False

    def should_notify(self, msg: IMMessage) -> bool:
        qq_cfg = self.config.get("qq") or {}
        if msg.msg_type == "group":
            if qq_cfg.get("group_at_only", True) and not msg.is_at_me:
                keywords = [k for k in (qq_cfg.get("keywords") or []) if k]
                if not any(k in msg.content for k in keywords):
                    return False
        if self.in_quiet_hours():
            return False
        return True

    def in_quiet_hours(self) -> bool:
        qh = self.config.get("quiet_hours") or {}
        return _in_quiet_hours(datetime.now(), str(qh.get("start", "23:00")), str(qh.get("end", "08:00")))

    # === 本地缓冲（滚动 7 天） ===
    def append_buffer(self, msg: IMMessage) -> None:
        try:
            with _BUFFER_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(msg), ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def rotate_buffer(self) -> None:
        """启动时调用：删掉超过保留期的行。"""
        if not _BUFFER_FILE.exists():
            return
        cutoff = datetime.now() - timedelta(days=_BUFFER_DAYS)
        kept: list[str] = []
        try:
            lines = _BUFFER_FILE.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            try:
                ts = float(json.loads(line).get("timestamp", 0))
            except (json.JSONDecodeError, ValueError):
                continue
            if datetime.fromtimestamp(ts) >= cutoff:
                kept.append(line)
        try:
            _BUFFER_FILE.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        except OSError:
            pass

    def recent_messages(self, hours: float = 2.0, limit: int = 50) -> list[IMMessage]:
        """读取近 hours 小时的缓冲消息（旧→新）。"""
        if not _BUFFER_FILE.exists():
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        out: list[IMMessage] = []
        try:
            for line in _BUFFER_FILE.read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if datetime.fromtimestamp(float(d.get("timestamp", 0))) >= cutoff:
                    d.pop("raw", None)
                    out.append(IMMessage(**d))
        except (OSError, TypeError, ValueError):
            return out
        return out[-limit:]
