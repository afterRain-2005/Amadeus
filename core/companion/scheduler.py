"""调度器：节流/静音/概率门控/每日上限/全局冷却/用户对话冷却。

执行顺序（在 Evaluator 之外，由 Controller 调用）：
1. enabled 检查
2. 静音时段检查（away 超 1h 例外）
3. 频率概率门控
4. 每日上限检查
5. 全局冷却检查
6. 用户对话冷却检查
"""
from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Optional


FREQ_RATIO = {"low": 0.2, "mid": 0.5, "high": 1.0}


class Scheduler:
    def __init__(self, config: dict) -> None:
        self.enabled = bool(config.get("enabled", True))
        qh = config.get("quiet_hours", {"start": "23:00", "end": "08:00"})
        self.quiet_start = self._parse_hour(qh.get("start", "23:00"))
        self.quiet_end = self._parse_hour(qh.get("end", "08:00"))
        self.frequency = str(config.get("frequency", "mid"))
        self.daily_limit = int(config.get("daily_limit", 30))
        proactive = config.get("proactive", {}) or {}
        self.global_cooldown_seconds = int(
            proactive.get("global_cooldown_seconds", config.get("global_cooldown_seconds", 600))
        )
        self.user_dialogue_cooldown_seconds = int(
            proactive.get(
                "user_dialogue_cooldown_seconds",
                config.get("user_dialogue_cooldown_seconds", 300),
            )
        )
        self.topic_cooldowns = dict(proactive.get("topic_cooldowns", {}) or {})
        budget = proactive.get("interrupt_budget", {}) or {}
        self.hard_interrupt_budget = int(budget.get("hard_per_day", self.daily_limit))
        self._topic_last_ts: dict[str, float] = {}
        self._interrupt_budget_used = 0
        self._budget_day = self._today_key()

    @staticmethod
    def _parse_hour(hhmm: str) -> float:
        try:
            h, m = hhmm.split(":")
            return int(h) + int(m) / 60
        except (ValueError, AttributeError):
            return 23.0

    def _in_quiet_hours(self, local_hour: float) -> bool:
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= local_hour < self.quiet_end
        else:
            # 跨午夜（如 23:00-08:00）
            return local_hour >= self.quiet_start or local_hour < self.quiet_end

    def should_consider(
        self,
        *,
        local_hour: float,
        idle_state: str = "active",
        idle_seconds: int = 0,
        greeting_count_today: int = 0,
    ) -> bool:
        if not self.enabled:
            return False
        # 静音时段：away 超 1h 例外
        if self._in_quiet_hours(local_hour):
            if not (idle_state == "away" and idle_seconds > 3600):
                return False
        # 每日上限
        if greeting_count_today >= self.daily_limit:
            return False
        if not self.interrupt_budget_allows():
            return False
        # 概率门控
        ratio = FREQ_RATIO.get(self.frequency, 0.5)
        if random.random() >= ratio:
            return False
        return True

    def global_cooldown_allows(
        self, *, last_greeting_ts_epoch: Optional[float], window_seconds: Optional[int] = None,
    ) -> bool:
        window = self.global_cooldown_seconds if window_seconds is None else window_seconds
        if last_greeting_ts_epoch is None:
            return True
        return (time.time() - last_greeting_ts_epoch) >= window

    def user_dialogue_cooldown_allows(
        self, *, last_user_msg_ts: Optional[float], window_seconds: Optional[int] = None,
    ) -> bool:
        window = (
            self.user_dialogue_cooldown_seconds
            if window_seconds is None
            else window_seconds
        )
        if last_user_msg_ts is None:
            return True
        return (time.time() - last_user_msg_ts) >= window

    def topic_cooldown_allows(self, topic: Optional[str]) -> bool:
        if not topic:
            return True
        window = int(self.topic_cooldowns.get(topic, 0) or 0)
        if window <= 0:
            return True
        last_ts = self._topic_last_ts.get(topic)
        if last_ts is None:
            return True
        return (time.time() - last_ts) >= window

    def interrupt_budget_allows(self) -> bool:
        self._reset_budget_if_needed()
        return self._interrupt_budget_used < self.hard_interrupt_budget

    def record_proactive(self, topic: Optional[str] = None) -> None:
        self._reset_budget_if_needed()
        self._interrupt_budget_used += 1
        if topic:
            self._topic_last_ts[topic] = time.time()

    def _reset_budget_if_needed(self) -> None:
        today = self._today_key()
        if today != self._budget_day:
            self._budget_day = today
            self._interrupt_budget_used = 0
            self._topic_last_ts.clear()

    @staticmethod
    def _today_key() -> str:
        return datetime.now().strftime("%Y-%m-%d")
