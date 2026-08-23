"""LLM 流式输出的内联标签提取与 TTS 旁白剥离。

移植自 airi (moeru-ai/airi) packages/pipelines-audio：
  - StreamTagSplitter   ≈ llm-streaming-control（<|ACT|>/<|DELAY|> 的 [...] 方言版）
  - NarrativeFilter     ≈ processors/tts-chunker.ts 的 processNarrative + 流式缓冲

设计目标：LLM 输出中的控制信息（[emotion:smile] / [motion:facepalm] /
[delay:1.5]）在流式阶段就被提取分发（说话中途换表情/做动作/停顿），
叙述文本（*歪头*、（笑）、【旁白】）不进 TTS；两者都要求**流式安全**——
标签或括号跨 chunk 到达、甚至永不闭合时不能卡住管线。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ============================================================
# 流式标签切分
# ============================================================

_TAG_RE = re.compile(r"\[(emotion|motion|delay):([^\]\n]*)\]")
# 标签跨 chunk 时的最长缓冲：超过即认为 "[" 只是普通文本，放行
_TAG_BUFFER_LIMIT = 64

VALID_EMOTIONS = frozenset({"neutral", "smile", "blush", "angry", "sad"})
# 动作词表与 live2d 页面 MOTIONS 字典一一对应（见 core/companion/expression.py）
VALID_MOTIONS = frozenset({
    "neutral", "smile", "blush", "angry", "sad", "thinking",
    "hands_on_hips", "arms_crossed", "facepalm", "shrug", "chin_rest",
})
_DELAY_MAX_SECONDS = 5.0


@dataclass(frozen=True)
class Tag:
    kind: str      # emotion / motion / delay
    value: str     # emotion/motion 名；delay 为秒数（字符串形式）
    seconds: float  # 仅 delay 有效，其余为 0

    @property
    def valid(self) -> bool:
        if self.kind == "emotion":
            return self.value in VALID_EMOTIONS
        if self.kind == "motion":
            return self.value in VALID_MOTIONS
        return self.kind == "delay" and 0 < self.seconds <= _DELAY_MAX_SECONDS


class StreamTagSplitter:
    """把流式 delta 切成 ('text', str) / ('tag', Tag) 事件序列。

    流式安全：行缓冲里保留最后一个未闭合的 "["（及其后文本）等闭合；
    超过 _TAG_BUFFER_LIMIT 字符仍无 "]" 时按普通文本放行。
    """

    def __init__(self) -> None:
        self._pending = ""

    def push(self, delta: str) -> list[tuple[str, object]]:
        self._pending += delta
        return self._drain(final=False)

    def flush(self) -> list[tuple[str, object]]:
        events = self._drain(final=True)
        rest = self._pending
        self._pending = ""
        if rest:
            events.append(("text", rest))
        return events

    def _drain(self, *, final: bool) -> list[tuple[str, object]]:
        events: list[tuple[str, object]] = []
        buf = self._pending
        out_text = ""
        pos = 0
        while pos < len(buf):
            bracket = buf.find("[", pos)
            if bracket < 0:
                out_text += buf[pos:]
                pos = len(buf)
                break
            out_text += buf[pos:bracket]
            match = _TAG_RE.match(buf, bracket)
            if match:
                events.append(("tag", _make_tag(match.group(1), match.group(2))))
                pos = match.end()
                continue
            # "[" 后暂无完整标签：未闭合则留缓冲等下个 chunk
            tail = buf[bracket:]
            if not final and "]" not in tail and len(tail) < _TAG_BUFFER_LIMIT:
                break
            # 已有 "]"（不匹配标签格式）或超长：当普通文本放行
            next_close = tail.find("]")
            if next_close >= 0:
                out_text += tail[: next_close + 1]
                pos = bracket + next_close + 1
            else:
                out_text += tail
                pos = len(buf)
        self._pending = buf[pos:]
        if out_text:
            events.insert(0, ("text", out_text))
        return events


def _make_tag(kind: str, raw_value: str) -> Tag:
    value = raw_value.strip()
    seconds = 0.0
    if kind == "delay":
        try:
            seconds = min(_DELAY_MAX_SECONDS, max(0.0, float(value)))
        except ValueError:
            seconds = 0.0
        value = f"{seconds:g}"
    return Tag(kind=kind, value=value, seconds=seconds)


# ============================================================
# 旁白剥离（airi tts-chunker processNarrative 移植）
# ============================================================

_BRACKET_MAP = {"[": "]", "(": ")", "（": "）", "【": "】", "<": ">"}
_OPENERS = frozenset(_BRACKET_MAP)
_CLOSERS = frozenset(_BRACKET_MAP.values())

# <...> 只有像表演标签时才算旁白，避免误伤数学/代码（a<b、<html> 等判定见下）
_NARRATIVE_KEYWORDS = (
    "laugh", "sigh", "action", "note", "breath", "giggle",
    "whisper", "cry", "smile", "thought",
    "笑", "叹", "旁白", "动作", "低语", "哭", "微笑", "心想",
)
_UNI_LETTER_RE = re.compile(r"\w", re.UNICODE)


def _is_probably_angle_tag(text: str, index: int) -> bool:
    """判定 text[index] 的 '<' 是否为旁白/表演标签的开头（airi 同款启发式）。"""
    if text[index] != "<":
        return False
    if index + 1 < len(text) and text[index + 1] == "/":
        return True  # 闭合标签 </...> 永远是标签
    remainder = text[index + 1:].lower()
    next_char = remainder[:1]
    prev_char = text[index - 1] if index > 0 else ""
    if next_char and re.match(r"[0-9\s=]", next_char):
        return False  # < 3 / < foo 形式，不是标签
    if prev_char and _UNI_LETTER_RE.match(prev_char):
        # 前面是字母/数字：只有后接旁白关键词才算（a<laugh>b 这类罕见写法）
        return any(
            (len(remainder) > 1 and kw.startswith(remainder)) or remainder.startswith(kw)
            for kw in _NARRATIVE_KEYWORDS
        )
    if prev_char and re.match(r"[^\s([{（【<\])}>）】.,!?;:，。！？；：'\"\-_]", prev_char):
        return False  # 前面是普通文字（代码/比较），不是标签
    return True


def process_narrative(text: str, *, keep_content: bool = False) -> str:
    """剥离叙述文本：*动作*、[注]、（笑）、【旁白】、<laugh>。

    keep_content=False：整段删除（默认，旁白不该被读出来）
    keep_content=True：只去掉定界符、保留内容（如想让 TTS 读出括号里的话）
    """
    ranges: list[tuple[int, int]] = []
    chars_to_drop: set[int] = set()
    stack: list[tuple[str, int]] = []
    star_open = -1

    for i, ch in enumerate(text):
        if ch == "*":
            if star_open >= 0:
                if keep_content:
                    chars_to_drop.update((star_open, i))
                else:
                    ranges.append((star_open, i))
                star_open = -1
            else:
                nxt = text[i + 1] if i + 1 < len(text) else ""
                if not nxt.isspace():
                    star_open = i
            continue
        if ch in _OPENERS:
            if ch == "<" and not _is_probably_angle_tag(text, i):
                continue
            stack.append((ch, i))
            continue
        if ch in _CLOSERS and stack and _BRACKET_MAP[stack[-1][0]] == ch:
            opener, open_index = stack.pop()
            if keep_content:
                chars_to_drop.update((open_index, i))
            else:
                ranges.append((open_index, i))

    if keep_content:
        return "".join(ch for i, ch in enumerate(text) if i not in chars_to_drop and ch != "*")

    ranges.sort()
    result = []
    range_idx = 0
    for i, ch in enumerate(text):
        while range_idx < len(ranges) and i > ranges[range_idx][1]:
            range_idx += 1
        if range_idx < len(ranges):
            lo, hi = ranges[range_idx]
            if lo <= i <= hi:
                continue
        result.append(ch)
    return "".join(result)


def _unclosed_narrative_state(text: str) -> tuple[bool, bool]:
    """返回 (有无未闭合定界, 未闭合的是否为旁白型括号)。"""
    stack: list[str] = []
    stars = 0
    for i, ch in enumerate(text):
        if ch == "*":
            nxt = text[i + 1] if i + 1 < len(text) else ""
            prv = text[i - 1] if i > 0 else ""
            if not nxt.isspace() and not prv.isspace():
                stars += 1
            continue
        if ch in _OPENERS:
            if ch == "<" and not _is_probably_angle_tag(text, i):
                continue
            stack.append(ch)
        elif ch in _CLOSERS and stack and _BRACKET_MAP[stack[-1]] == ch:
            stack.pop()
    narrative_open = any(c in ("[", "【", "<", "（") for c in stack)
    return bool(stack) or stars % 2 == 1, narrative_open


class NarrativeFilter:
    """流式旁白剥离：定界符未闭合时缓冲，闭合或超限时放行。

    airi 同款兜底：普通未闭合 200 字放行；旁白型括号未闭合放宽到 800 字
    （旁白内容一般较长，过早放行会把标签念出来）。
    """

    _LIMIT = 200
    _NARRATIVE_LIMIT = 800

    def __init__(self) -> None:
        self._pending = ""

    def reset(self) -> None:
        self._pending = ""

    def push(self, delta: str) -> str:
        self._pending += delta
        unclosed, narrative = _unclosed_narrative_state(self._pending)
        limit = self._NARRATIVE_LIMIT if narrative else self._LIMIT
        if unclosed and len(self._pending) < limit:
            return ""
        out = process_narrative(self._pending)
        self._pending = ""
        return out

    def flush(self) -> str:
        out = process_narrative(self._pending)
        self._pending = ""
        return out
