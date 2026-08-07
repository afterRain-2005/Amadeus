"""会话级登录态（内存变量，不持久化）。

对应原 amadeus/src/lib/auth.ts。
退出程序后丢失，重启必须重新登录。
"""
from __future__ import annotations

from config import DEFAULT_CHARACTER


_session_logged_in: bool = False
_session_character_id: str = DEFAULT_CHARACTER.id


def is_session_logged_in() -> bool:
    return _session_logged_in


def set_session_logged_in(value: bool) -> None:
    global _session_logged_in
    _session_logged_in = value


def get_session_character_id() -> str:
    return _session_character_id


def set_session_character_id(character_id: str) -> None:
    global _session_character_id
    _session_character_id = character_id
