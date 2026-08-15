"""评估器：L1 硬阈值规则（必说场景走模板）+ L2 LLM 决策（可选场景）。

LLM 调用节流：5min 内同类信号不重复（由 Scheduler 上层控制，Evaluator 只在调用时记录）。
LLM 失败降级：返回 fallback_template（即便本场景非必说）。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from core.companion.prompts import (
    KURISU_PROACTIVE_INSTRUCTION, KURISU_PROACTIVE_TEMPLATES,
)
from core.companion.sensors import ContextSnapshot


@dataclass
class GreetingDecision:
    text: str
    emotion: str
    topic: str
    source: str  # 'template' | 'llm' | 'fallback_template'


def _call_llm(snapshot: ContextSnapshot, *, endpoint: str, api_key: str, model: str) -> dict:
    """调 LLM 决策器，返回解析后的 dict。失败抛异常。"""
    system = KURISU_PROACTIVE_INSTRUCTION
    user_msg = json.dumps({
        "timestamp": snapshot.timestamp,
        "local_time": snapshot.local_time,
        "is_deep_night": snapshot.is_deep_night,
        "idle_seconds": snapshot.idle_seconds,
        "work_session_minutes": snapshot.work_session_minutes,
        "idle_state": snapshot.idle_state,
        "active_window_title": snapshot.active_window_title,
        "active_process": snapshot.active_process,
        "window_changed_recently": snapshot.window_changed_recently,
        "last_companion_topic": snapshot.last_companion_topic,
        "greeting_count_today": snapshot.greeting_count_today,
        "clipboard_preview": snapshot.clipboard_preview,
    }, ensure_ascii=False)
    with httpx.Client(timeout=5) as client:
        resp = client.post(
            f"{endpoint.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": 100,
                "temperature": 0.8,
                "response_format": {"type": "json_object"},
            },
        )
    if resp.is_error:
        raise OSError(f"LLM HTTP {resp.status_code}")
    content = resp.json()["choices"][0]["message"]["content"]
    data = json.loads(content)  # 失败抛 ValueError
    return data


class Evaluator:
    """评估器：L1 硬阈值 → L2 LLM 决策。"""

    def __init__(self) -> None:
        # signal_type -> last_llm_call_ts，节流用（5min 内同类不重复）
        self._last_llm_call_ts: dict[str, float] = {}

    def evaluate(
        self,
        snapshot: ContextSnapshot,
        *,
        allow_llm: bool = True,
        signal_type: str = "default",
        llm_endpoint: str = "",
        llm_api_key: str = "",
        llm_model: str = "",
    ) -> Optional[GreetingDecision]:
        # L1: 硬阈值规则引擎（必说场景，零 LLM 成本）
        decision = self._hard_rules(snapshot)
        if decision:
            return decision

        if not allow_llm:
            return None

        # L2: LLM 决策（5min 节流）
        if not self._llm_throttle_allows(signal_type):
            return None

        return self._llm_decide(
            snapshot, signal_type=signal_type,
            endpoint=llm_endpoint, api_key=llm_api_key, model=llm_model,
        )

    def _hard_rules(self, snapshot: ContextSnapshot) -> Optional[GreetingDecision]:
        """L1 硬阈值规则：返回首个命中的模板。"""
        for tpl in KURISU_PROACTIVE_TEMPLATES:
            if tpl["condition"](snapshot):
                text = tpl["text"].format(
                    local_time=snapshot.local_time,
                    work_session_minutes=snapshot.work_session_minutes,
                )
                return GreetingDecision(
                    text=text, emotion=tpl["emotion"], topic=tpl["topic"],
                    source="template",
                )
        return None

    def _llm_throttle_allows(self, signal_type: str, window_seconds: int = 300) -> bool:
        """5min 内同类信号不重复调 LLM。"""
        last = self._last_llm_call_ts.get(signal_type, 0)
        if (time.time() - last) < window_seconds:
            return False
        self._last_llm_call_ts[signal_type] = time.time()
        return True

    def _llm_decide(
        self, snapshot: ContextSnapshot, *, signal_type: str,
        endpoint: str, api_key: str, model: str,
    ) -> Optional[GreetingDecision]:
        try:
            data = _call_llm(snapshot, endpoint=endpoint, api_key=api_key, model=model)
        except (OSError, ValueError, KeyError):
            # LLM 失败降级走 idle 模板兜底
            return GreetingDecision(
                text="盯着屏幕发呆也修不好 bug，不如起来走走？",
                emotion="idle", topic="idle", source="fallback_template",
            )
        if not data.get("should_speak"):
            return None
        return GreetingDecision(
            text=data.get("text", ""),
            emotion=data.get("emotion", "neutral"),
            topic=data.get("topic", "general"),
            source="llm",
        )
