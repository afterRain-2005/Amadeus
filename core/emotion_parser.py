"""Parse Amadeus bilingual replies and their Live2D emotion tag."""
from __future__ import annotations

from dataclasses import dataclass
import re


_EMOTION_RE = re.compile(r"^\[emotion:(neutral|blush|angry|smile|sad)\]")


@dataclass(frozen=True)
class ParsedReply:
    chinese: str
    japanese: str
    emotion: str


def parse_reply(raw: str) -> ParsedReply:
    text = raw.strip()
    match = _EMOTION_RE.match(text)
    emotion = match.group(1) if match else "neutral"
    if match:
        text = text[match.end():].strip()
    parts = re.split(r"\r?\n===\r?\n", text, maxsplit=1)
    if len(parts) == 2:
        return ParsedReply(parts[0].strip(), parts[1].strip(), emotion)
    return ParsedReply(text, "", emotion)
