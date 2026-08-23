"""Parse Amadeus bilingual replies and their Live2D emotion tag."""
from __future__ import annotations

from dataclasses import dataclass
import re


_EMOTION_RE = re.compile(
    r"^\[emotion:(neutral|smile|blush|angry|sad|thinking|surprised|laugh|sleepy|confused)\]"
)


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

    # LLM 双语输出可能含多个 === 段，中文散布在各段之间（如：
    #   中文1 === 日语1 [emotion]中文2 === 日语2 中文3 ...）。
    # 只用第一个 === 会把中文2/3 丢进日语，导致中文被截断。
    # 正确做法：按 === 切所有段 → 去掉 [emotion] 标签 → 按空白行切块 →
    # 用假名判断（日语必有假名 U+3040-309F/U+30A0-30FF，纯汉字无法区分但
    # 日语段必含假名）区分中/日，再分别合并。
    chinese_parts: list[str] = []
    japanese_parts: list[str] = []
    for segment in re.split(r"\r?\n===\r?\n", text):
        segment = re.sub(r"\[emotion:[^\]]+\]", "", segment)
        for chunk in re.split(r"\n\s*\n", segment):
            chunk = chunk.strip()
            if not chunk:
                continue
            if re.search(r"[\u3040-\u309F\u30A0-\u30FF]", chunk):
                japanese_parts.append(chunk)
            else:
                chinese_parts.append(chunk)

    return ParsedReply(
        "\n\n".join(chinese_parts),
        "\n\n".join(japanese_parts),
        emotion,
    )
