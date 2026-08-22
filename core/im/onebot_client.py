"""QQ OneBot 11 WebSocket 客户端（对接 NapCat / Lagrange / LLOneBot 等）。

独立线程跑 asyncio loop：连接 → 收事件 → 解析为 IMMessage 回调上报；
断线指数退避重连（1s→2s→…→60s 封顶），stop() 可干净退出。
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Callable

from core.im.models import IMMessage, parse_onebot_event

# 状态回调语义：(state, detail) —— state: connecting / connected / disconnected / error
StatusCallback = Callable[[str, str], None]
MessageCallback = Callable[[IMMessage], None]


class OneBotClient:
    """正向 WS 客户端（连 NapCat 暴露的 ws://127.0.0.1:3001）。"""

    def __init__(self, ws_url: str, on_message: MessageCallback, on_status: StatusCallback) -> None:
        self.ws_url = ws_url
        self.on_message = on_message
        self.on_status = on_status
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # === 生命周期 ===
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="amadeus-im-onebot", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        thread = self._thread
        if thread:
            thread.join(timeout=3)
        self._thread = None
        self._loop = None

    # === 内部：asyncio 主循环 ===
    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._loop = asyncio.new_event_loop()
            try:
                self._loop.run_until_complete(self._connect_loop())
            except Exception as exc:  # noqa: BLE001 —— 后台线程兜底，任何异常都不能杀进程
                self._safe_status("error", str(exc))
            finally:
                try:
                    self._loop.close()
                except Exception:
                    pass
                self._loop = None
            if not self._stop_event.is_set():
                # loop 被外部 stop 时直接退出；否则 _connect_loop 已处理退避
                self._stop_event.wait(1.0)

    async def _connect_loop(self) -> None:
        """连接 + 重连循环，退避 1s 起步翻倍、60s 封顶。"""
        import websockets

        backoff = 1.0
        while not self._stop_event.is_set():
            self._safe_status("connecting", self.ws_url)
            try:
                async with websockets.connect(self.ws_url, open_timeout=8) as ws:
                    self._safe_status("connected", self.ws_url)
                    backoff = 1.0
                    async for raw in ws:
                        self._handle_raw(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._safe_status("disconnected", str(exc))
            if self._stop_event.is_set():
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    def _handle_raw(self, raw: str | bytes) -> None:
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(event, dict):
            return
        msg = parse_onebot_event(event)
        if msg is not None:
            try:
                self.on_message(msg)
            except Exception:  # noqa: BLE001 —— 回调异常不砸 WS 循环
                pass

    def _safe_status(self, state: str, detail: str) -> None:
        try:
            self.on_status(state, detail)
        except Exception:  # noqa: BLE001
            pass
