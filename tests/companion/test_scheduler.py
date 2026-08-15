"""Scheduler 测试：节流/冷却/静音/概率门控/每日上限。"""
from datetime import datetime, timezone
from unittest.mock import patch

from core.companion.scheduler import Scheduler


def test_scheduler_allows_when_all_conditions_met():
    s = Scheduler(config={
        "enabled": True,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "frequency": "high",  # 100% 概率门控透明，验证其他条件全通过
        "daily_limit": 30,
    })
    # 14:30 不在静音时段
    assert s.should_consider(local_hour=14) is True


def test_scheduler_blocks_in_quiet_hours():
    s = Scheduler(config={
        "enabled": True,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
        "frequency": "mid", "daily_limit": 30,
    })
    # 02:30 在静音时段
    assert s.should_consider(local_hour=2) is False


def test_scheduler_blocks_when_disabled():
    s = Scheduler(config={"enabled": False})
    assert s.should_consider(local_hour=14) is False


def test_scheduler_frequency_low_20_percent():
    """low=20% 概率门控：random < 0.2 才允许。"""
    s = Scheduler(config={
        "enabled": True, "frequency": "low", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    with patch("core.companion.scheduler.random.random", return_value=0.1):
        assert s.should_consider(local_hour=14) is True
    with patch("core.companion.scheduler.random.random", return_value=0.3):
        assert s.should_consider(local_hour=14) is False


def test_scheduler_frequency_high_100_percent():
    s = Scheduler(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    with patch("core.companion.scheduler.random.random", return_value=0.99):
        assert s.should_consider(local_hour=14) is True


def test_scheduler_blocks_when_daily_limit_reached():
    s = Scheduler(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    assert s.should_consider(local_hour=14, greeting_count_today=30) is False
    assert s.should_consider(local_hour=14, greeting_count_today=29) is True


def test_scheduler_global_cooldown_10min():
    """上次问候后 10min 内不重复。"""
    s = Scheduler(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    # 模拟上次问候在 5min 前
    import time
    last_ts = (datetime.now(timezone.utc).timestamp() - 300)  # 5min 前
    assert s.global_cooldown_allows(last_greeting_ts_epoch=last_ts) is False
    # 11min 前
    last_ts = (datetime.now(timezone.utc).timestamp() - 660)
    assert s.global_cooldown_allows(last_greeting_ts_epoch=last_ts) is True


def test_scheduler_user_dialogue_cooldown_5min():
    """用户对话后 5min 内不触发 companion。"""
    s = Scheduler(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    import time
    now = time.time()
    # 用户 3min 前发过消息
    assert s.user_dialogue_cooldown_allows(last_user_msg_ts=now - 180) is False
    # 6min 前
    assert s.user_dialogue_cooldown_allows(last_user_msg_ts=now - 360) is True


def test_scheduler_quiet_hours_away_exception():
    """静音时段但 idle_state=away 超 1h 例外触发。"""
    s = Scheduler(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    # 02:30 在静音时段，但 away 超 1h
    assert s.should_consider(
        local_hour=2, idle_state="away", idle_seconds=3700) is True
    # 02:30 静音时段，正常活动
    assert s.should_consider(local_hour=2, idle_state="active") is False
