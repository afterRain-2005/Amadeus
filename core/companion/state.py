"""Presence state for the desktop companion.

The first version is intentionally rule-based and in-memory.  It turns the
existing sensor snapshot into a continuous companion state that other systems
can use without coupling themselves to raw idle timers.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Optional

from core.companion.sensors import ContextSnapshot


@dataclass(frozen=True)
class PresenceState:
    mood: str = "neutral"
    energy: int = 70
    attention: str = "watching"
    focus_state: str = "casual"
    last_event: str = "startup"
    last_updated_ts: float = 0.0
    focus_minutes: int = 0
    idle_seconds: int = 0
    interrupt_budget_used: int = 0


class PresenceStateEngine:
    """Small state machine that smooths raw desktop context into presence."""

    def __init__(self, config: Optional[dict] = None) -> None:
        self.config = config or {}
        self.enabled = bool(self.config.get("enabled", True))
        self.focus_minutes = int(self.config.get("focus_minutes", 25))
        self.deep_focus_minutes = int(self.config.get("deep_focus_minutes", 45))
        self.state = PresenceState(last_updated_ts=time.time())

    def update_from_snapshot(
        self, snapshot: ContextSnapshot, *, local_hour: float
    ) -> PresenceState:
        if not self.enabled:
            return self.state

        focus_state = self._classify_focus(snapshot)
        attention = self._classify_attention(snapshot, focus_state)
        mood = self._classify_mood(snapshot, focus_state, local_hour)
        energy = self._estimate_energy(snapshot, local_hour)
        event = self._last_event(snapshot, focus_state)

        self.state = replace(
            self.state,
            mood=mood,
            energy=energy,
            attention=attention,
            focus_state=focus_state,
            last_event=event,
            last_updated_ts=time.time(),
            focus_minutes=max(0, int(snapshot.work_session_minutes)),
            idle_seconds=max(0, int(snapshot.idle_seconds)),
        )
        return self.state

    def on_user_message(self) -> PresenceState:
        self.state = replace(
            self.state,
            attention="engaged",
            mood="curious" if self.state.mood == "neutral" else self.state.mood,
            last_event="user_message",
            last_updated_ts=time.time(),
        )
        return self.state

    def on_call_started(self) -> PresenceState:
        self.state = replace(
            self.state,
            attention="in_call",
            last_event="call_started",
            last_updated_ts=time.time(),
        )
        return self.state

    def on_call_ended(self) -> PresenceState:
        self.state = replace(
            self.state,
            attention="watching",
            last_event="call_ended",
            last_updated_ts=time.time(),
        )
        return self.state

    def record_interrupt(self) -> PresenceState:
        self.state = replace(
            self.state,
            interrupt_budget_used=self.state.interrupt_budget_used + 1,
            last_event="proactive_interrupt",
            last_updated_ts=time.time(),
        )
        return self.state

    def _classify_focus(self, snapshot: ContextSnapshot) -> str:
        if snapshot.idle_seconds >= 300:
            return "stale"
        minutes = snapshot.work_session_minutes
        if minutes >= self.deep_focus_minutes:
            return "deep_focus"
        if minutes >= self.focus_minutes:
            return "focused"
        return "casual"

    @staticmethod
    def _classify_attention(snapshot: ContextSnapshot, focus_state: str) -> str:
        if snapshot.idle_state in ("idle", "away"):
            return "idle"
        if focus_state in ("focused", "deep_focus"):
            return "focused"
        return "watching"

    def _classify_mood(
        self, snapshot: ContextSnapshot, focus_state: str, local_hour: float
    ) -> str:
        if snapshot.idle_state == "away":
            return "neutral"
        if snapshot.is_deep_night or local_hour < 6:
            return "tired"
        if focus_state == "deep_focus":
            return "focused"
        if snapshot.window_changed_recently and snapshot.work_session_minutes < 10:
            return "curious"
        return "neutral"

    @staticmethod
    def _estimate_energy(snapshot: ContextSnapshot, local_hour: float) -> int:
        energy = 75
        if snapshot.is_deep_night or local_hour < 6:
            energy -= 25
        if snapshot.work_session_minutes >= 60:
            energy -= min(30, (snapshot.work_session_minutes - 45) // 3)
        if snapshot.idle_state == "away":
            energy -= 10
        return max(0, min(100, int(energy)))

    @staticmethod
    def _last_event(snapshot: ContextSnapshot, focus_state: str) -> str:
        if snapshot.idle_state == "away":
            return "away"
        if snapshot.idle_state == "idle":
            return "idle"
        if snapshot.window_changed_recently:
            return "window_changed"
        if focus_state in ("focused", "deep_focus"):
            return focus_state
        return "active"
