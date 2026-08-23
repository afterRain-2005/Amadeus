"""对话历史压缩：保留最近 N 个 turn 原文，旧 turn 替换为摘要行。

移植自 airi (moeru-ai/airi) packages/core-agent/src/messages/compaction.ts。

解决什么：长会话的 messages 列表线性膨胀，逐条塞 prompt 吃 token。
简单截断（history[-12:]）会无提示地丢上下文；压缩改为：
  [摘要消息（"已压缩 N 轮旧对话"）] + [最近 keep_turns 轮原文]
摘要函数可注入（例如以后接 LLM 生成真正摘要），默认计数式占位。
"""
from __future__ import annotations

from typing import Callable

# turn = 一条 user 消息及其后的连续 assistant 消息
Summarizer = Callable[[int, list[dict]], str]


def _default_summarizer(removed_turns: int, removed: list[dict]) -> str:
    return f"[已压缩 {removed_turns} 轮较早的对话，仅保留最近内容]"


def compact_history(
    messages: list[dict],
    *,
    keep_turns: int = 12,
    summarizer: Summarizer | None = None,
) -> list[dict]:
    """返回压缩后的新列表（不修改原列表）。

    keep_turns <= 0 或 turn 数未超限时原样返回（浅拷贝）。
    消息按时间序排列，turn 以 role == "user" 计。
    """
    if keep_turns <= 0:
        return list(messages)

    total_turns = sum(1 for m in messages if m.get("role") == "user")
    if total_turns <= keep_turns:
        return list(messages)

    # 从尾往前数 keep_turns 个 user 消息，找到保留起点
    seen_turns = 0
    keep_from = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user":
            seen_turns += 1
            if seen_turns >= keep_turns:
                keep_from = index
                break

    removed = messages[:keep_from]
    removed_turns = sum(1 for m in removed if m.get("role") == "user")
    summary_text = (summarizer or _default_summarizer)(removed_turns, removed)

    compacted = [{"role": "system", "content": summary_text}]
    # system 摘要放在保留段开头（调用方通常在最前再加真正的 system 人设，
    # 两条 system 相邻无害且语义清晰）
    compacted.extend(messages[keep_from:])
    return compacted
