"""ASR 转写碎片缓冲：把停顿窗口内的多个识别结果归并为一个对话 turn。

移植自 airi (moeru-ai/airi) packages/pipelines-audio/src/transcript-buffer.ts。

解决什么：VAD 按停顿切段，用户中途换气/迟疑一下，半句话就会被当成
完整输入发给 LLM（"那个" 单独得到一个回复）。缓冲在 flush_delay 窗口内
归并碎片，自然停顿仍是同一句话；窗口一到或文本超上限立即下发。

CJK 边界拼接：中日韩文字之间不加空格，拉丁/其他之间加空格——
"你好"+"世界" → "你好世界"；"hello"+"world" → "hello world"。

定时器用 threading.Timer（voice_call 的转写回调跑在后台线程，无 Qt 事件环）。
"""
from __future__ import annotations

import re
import threading
from typing import Callable

_CJK_BOUNDARY_RE = re.compile(r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]$")
_CJK_START_RE = re.compile(r"^[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]")


def join_transcript_fragments(previous: str, nxt: str) -> str:
    """按书写系统决定拼接处是否加空格（CJK 不加，其余加）。"""
    if not previous:
        return nxt
    if _CJK_BOUNDARY_RE.search(previous) and _CJK_START_RE.match(nxt):
        return f"{previous}{nxt}"
    return f"{previous} {nxt}"


class TranscriptBuffer:
    """停顿归并缓冲。flush 回调可能异步，内部串行化保证顺序下发。"""

    def __init__(
        self,
        flush: Callable[[str], None],
        *,
        flush_delay_ms: int = 700,
        max_buffered_len: int = 80,
    ) -> None:
        self._flush_cb = flush
        self._flush_delay = max(0.0, flush_delay_ms / 1000.0)
        self._max_len = max_buffered_len
        self._pending = ""
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def push(self, text: str) -> None:
        trimmed = text.strip()
        if not trimmed:
            return
        with self._lock:
            self._pending = join_transcript_fragments(self._pending, trimmed)
            over_limit = len(self._pending) >= self._max_len
            if not over_limit:
                self._schedule_flush_locked()
        if over_limit:
            self.flush_now()

    def flush_now(self) -> None:
        with self._lock:
            self._cancel_timer_locked()
            text = self._pending.strip()
            self._pending = ""
        if text:
            self._flush_cb(text)

    def clear(self) -> None:
        """丢弃未下发的文本（改口/打断/回合作废）。"""
        with self._lock:
            self._cancel_timer_locked()
            self._pending = ""

    # ---- 内部 ----

    def _schedule_flush_locked(self) -> None:
        self._cancel_timer_locked()
        if self._flush_delay <= 0:
            return
        self._timer = threading.Timer(self._flush_delay, self._on_timer)
        self._timer.daemon = True
        self._timer.start()

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _on_timer(self) -> None:
        self.flush_now()
