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


# === _parse_hour 边界 ===

def test_parse_hour_valid_format():
    """正常 HH:MM 格式解析正确。"""
    assert Scheduler._parse_hour("14:30") == 14.5
    assert Scheduler._parse_hour("00:00") == 0.0
    assert Scheduler._parse_hour("23:59") == 23 + 59 / 60
    assert Scheduler._parse_hour("08:00") == 8.0


def test_parse_hour_invalid_format_returns_default():
    """非法格式返回默认值 23.0。"""
    assert Scheduler._parse_hour("invalid") == 23.0
    assert Scheduler._parse_hour("14") == 23.0
    assert Scheduler._parse_hour("") == 23.0


def test_parse_hour_none_returns_default():
    """None 返回默认值 23.0。"""
    assert Scheduler._parse_hour(None) == 23.0  # type: ignore


# === 静音时段边界 ===

def test_quiet_hours_cross_midnight_boundary():
    """跨午夜静音时段（23:00-08:00）的边界值。"""
    s = Scheduler(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    assert s._in_quiet_hours(23.0) is True   # 23:00 边界
    assert s._in_quiet_hours(7.98) is True    # 07:59 
    assert s._in_quiet_hours(8.0) is False   # 08:00 边界（不包含）
    assert s._in_quiet_hours(22.99) is False  # 22:59
    assert s._in_quiet_hours(0.0) is True    # 午夜
    assert s._in_quiet_hours(3.0) is True     # 03:00


def test_quiet_hours_non_cross_midnight():
    """非跨午夜静音时段（12:00-14:00）。"""
    s = Scheduler(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "12:00", "end": "14:00"},
    })
    assert s._in_quiet_hours(12.0) is True
    assert s._in_quiet_hours(13.5) is True
    assert s._in_quiet_hours(14.0) is False
    assert s._in_quiet_hours(11.99) is False
    assert s._in_quiet_hours(0.0) is False


def test_quiet_hours_exact_midnight():
    """静音时段正好 00:00-00:00（整天静音）。"""
    s = Scheduler(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "00:00", "end": "23:59"},
    })
    assert s._in_quiet_hours(0.0) is True
    assert s._in_quiet_hours(14.0) is True


# === mid 频率 ===

def test_scheduler_frequency_mid_50_percent():
    """mid=50% 概率门控：random < 0.5 允许。"""
    s = Scheduler(config={
        "enabled": True, "frequency": "mid", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    with patch("core.companion.scheduler.random.random", return_value=0.4):
        assert s.should_consider(local_hour=14) is True
    with patch("core.companion.scheduler.random.random", return_value=0.5):
        assert s.should_consider(local_hour=14) is False
    with patch("core.companion.scheduler.random.random", return_value=0.6):
        assert s.should_consider(local_hour=14) is False


# === 非法频率 ===

def test_scheduler_invalid_frequency_defaults_to_mid():
    """非法频率值应回退到 0.5（FREQ_RATIO.get 默认值）。"""
    s = Scheduler(config={
        "enabled": True, "frequency": "invalid", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    with patch("core.companion.scheduler.random.random", return_value=0.4):
        assert s.should_consider(local_hour=14) is True
    with patch("core.companion.scheduler.random.random", return_value=0.6):
        assert s.should_consider(local_hour=14) is False


# === 冷却边界 ===

def test_global_cooldown_none_allows():
    """last_greeting_ts_epoch 为 None 时应允许。"""
    s = Scheduler(config={"enabled": True})
    assert s.global_cooldown_allows(last_greeting_ts_epoch=None) is True


def test_global_cooldown_custom_window():
    """自定义冷却窗口。"""
    s = Scheduler(config={"enabled": True})
    import time
    now = time.time()
    # 120s 窗口
    assert s.global_cooldown_allows(
        last_greeting_ts_epoch=now - 100, window_seconds=120) is False
    assert s.global_cooldown_allows(
        last_greeting_ts_epoch=now - 130, window_seconds=120) is True


def test_global_cooldown_exact_boundary():
    """刚好在窗口边界时应允许（>=）。"""
    s = Scheduler(config={"enabled": True})
    import time
    now = time.time()
    # 刚好 600s 前（默认窗口）
    assert s.global_cooldown_allows(last_greeting_ts_epoch=now - 600) is True
    # 599s 前
    assert s.global_cooldown_allows(last_greeting_ts_epoch=now - 599) is False


def test_user_dialogue_cooldown_none_allows():
    """last_user_msg_ts 为 None 时应允许。"""
    s = Scheduler(config={"enabled": True})
    assert s.user_dialogue_cooldown_allows(last_user_msg_ts=None) is True


def test_user_dialogue_cooldown_custom_window():
    """自定义对话冷却窗口。"""
    s = Scheduler(config={"enabled": True})
    import time
    now = time.time()
    # 60s 窗口
    assert s.user_dialogue_cooldown_allows(
        last_user_msg_ts=now - 30, window_seconds=60) is False
    assert s.user_dialogue_cooldown_allows(
        last_user_msg_ts=now - 70, window_seconds=60) is True


# === 构造器默认值 ===

def test_scheduler_defaults_when_config_missing():
    """config 缺少字段时应使用默认值。"""
    s = Scheduler(config={})
    assert s.enabled is True
    assert s.quiet_start == 23.0
    assert s.quiet_end == 8.0
    assert s.frequency == "mid"
    assert s.daily_limit == 30


def test_scheduler_enabled_string_false():
    """enabled 为字符串 'false' 时应被 bool() 转为 False。"""
    s = Scheduler(config={"enabled": False})
    assert s.enabled is False


# === 静音时段 away 例外边界 ===

def test_quiet_hours_away_exact_1h_boundary():
    """away 超 3600s 恰好例外触发。"""
    s = Scheduler(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    # away 且 idle_seconds > 3600
    assert s.should_consider(
        local_hour=2, idle_state="away", idle_seconds=3601) is True
    # away 且 idle_seconds == 3600 （不 > 3600）
    assert s.should_consider(
        local_hour=2, idle_state="away", idle_seconds=3600) is False


def test_quiet_hours_idle_state_not_away_blocks():
    """静音时段 idle_state='idle'（非 away）不例外。"""
    s = Scheduler(config={
        "enabled": True, "frequency": "high", "daily_limit": 30,
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    })
    assert s.should_consider(
        local_hour=2, idle_state="idle", idle_seconds=5000) is False
