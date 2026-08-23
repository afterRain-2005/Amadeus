# core/screen_context.py
"""普通聊天管线的屏幕感知（airi "see your screen" 对标）。

通话模式的屏幕共享走 core/voice_call.py；本模块补齐普通文字对话：
发送消息前截一帧 → OpenAI 兼容视觉模型生成一句话描述 → 以
"[屏幕感知] ..." 前缀注入 system prompt，让 AI 知道用户正在做什么。

隐私边界：
- 默认关闭（SCREEN_AWARENESS_DEFAULTS.enabled=False），设置页显式开启；
- 描述按 interval_seconds 缓存（默认 120s），不逐条消息重复截屏/请求；
- 视觉端点/key 未配置或任何异常 → 返回空字符串，主管线零影响。

截帧方式：一次性 mss 抓取主显示器（不复用 ScreenCapturer 常驻线程，
聊天频率低，按需抓取更省资源）。
"""
from __future__ import annotations

import threading
import time

from core.vision_client import describe_screen

# 进程内缓存：{"text": str, "at": float} + 锁（AgentTask 在线程池里跑）
_cache = {"text": "", "at": 0.0}
_lock = threading.Lock()


def screen_awareness_config(config: dict) -> dict:
    """全局配置 → 屏幕感知参数。vision 字段留空回退电话模式（phone）配置。"""
    defaults = {
        "enabled": False,
        "interval_seconds": 120,
        "vision_endpoint": "",
        "vision_api_key": "",
        "vision_model": "gpt-4o",
    }
    cfg = {**defaults, **(config.get("screen_awareness") if isinstance(config.get("screen_awareness"), dict) else {})}
    phone_cfg = config.get("phone") if isinstance(config.get("phone"), dict) else {}
    if not cfg.get("vision_endpoint"):
        cfg["vision_endpoint"] = str(phone_cfg.get("vision_endpoint", "") or "")
    if not cfg.get("vision_api_key"):
        cfg["vision_api_key"] = str(phone_cfg.get("vision_api_key", "") or "")
    return cfg


def reset_screen_context_cache() -> None:
    """清空描述缓存（设置页改动后立即生效 / 测试用）。"""
    with _lock:
        _cache["text"] = ""
        _cache["at"] = 0.0


def describe_current_screen(config: dict, *, force: bool = False) -> str:
    """当前屏幕 → 一句话描述（带缓存）。不可用/失败返回 ""。

    参数：
      config dict 全局配置（读 screen_awareness 与 phone 兜底键）
      force  bool 跳过缓存强制重新截屏描述
    返回值：str —— 形如"用户在写代码，IDE 打开了 memory.py"；失败为空串。
    """
    cfg = screen_awareness_config(config)
    if not cfg.get("enabled"):
        return ""
    try:
        interval = max(15.0, float(cfg.get("interval_seconds", 120) or 120))
    except (TypeError, ValueError):
        interval = 120.0
    with _lock:
        cached_text = _cache["text"]
        cached_at = _cache["at"]
    # 缓存有效性按时间戳判断（失败空结果也算已缓存，短周期内不重试）
    if not force and cached_at > 0 and time.time() - cached_at < interval:
        return cached_text

    text = ""
    frame = None
    try:
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            frame = sct.grab(monitor)
    except Exception:
        frame = None
    if frame is not None:
        text = describe_screen(
            frame,
            endpoint=str(cfg.get("vision_endpoint") or ""),
            api_key=str(cfg.get("vision_api_key") or ""),
            model=str(cfg.get("vision_model") or "gpt-4o"),
        )
    with _lock:
        # 失败也写缓存（短周期重试无意义），但空串不覆盖非空旧值
        if text or not _cache["text"]:
            _cache["text"] = text
            _cache["at"] = time.time()
    return text


def build_screen_prompt(config: dict, *, force: bool = False) -> str:
    """屏幕描述 → 注入 system prompt 的片段；不可用返回空串。"""
    text = describe_current_screen(config, force=force)
    if not text:
        return ""
    return f"[屏幕感知] 用户当前屏幕：{text}"
