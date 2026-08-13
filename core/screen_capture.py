# core/screen_capture.py
"""屏幕共享：mss 定时截帧，仅缓存最新帧。

设计：旁路异步截帧，不阻塞语音管线。通话态每 2.5s 截一帧，
仅保留最新帧（省内存）。用户说话结束时取最新帧附给视觉模型。
"""
from __future__ import annotations

import threading
import time
from typing import Any

import mss


class ScreenCapturer:
    """定时截屏缓存最新帧。线程安全。"""

    def __init__(self, interval_ms: int = 2500) -> None:
        self.interval = interval_ms / 1000.0
        self._latest: Any | None = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def latest_frame(self) -> Any | None:
        with self._lock:
            return self._latest

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                while self._running:
                    try:
                        frame = sct.grab(monitor)
                        with self._lock:
                            self._latest = frame
                    except Exception:
                        pass
                    time.sleep(self.interval)
        except Exception:
            pass

    def clear(self) -> None:
        with self._lock:
            self._latest = None
