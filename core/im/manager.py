"""IMManager：组装 OneBot 客户端与过滤器，经 Qt signal 投递到主线程。

用法（desktop_pet.run_overlay 内）：
    im = IMManager(parent=pet)
    im.message_notified.connect(...)   # 参数 IMMessage
    im.status_changed.connect(...)     # 参数 (state, detail)
    im.start()

客户端线程 emit signal 跨线程安全（Qt 自动队列连接），UI 侧槽在主线程执行。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from config import IM_DEFAULTS
from core.im.filter import MessageFilter
from core.im.models import IMMessage
from core.im.onebot_client import OneBotClient
from core.storage import load_config


def load_im_config() -> dict:
    """合并默认值后的 im 配置（设置页保存后即时生效需 restart）。"""
    cfg = load_config()
    return {**IM_DEFAULTS, **(cfg.get("im") or {})}


class IMManager(QObject):
    message_notified = Signal(object)      # IMMessage，应弹通知
    message_received = Signal(object)      # IMMessage，全部消息（含被过滤）
    status_changed = Signal(str, str)      # (state, detail)：connecting/connected/disconnected/error

    def __init__(self, parent: QObject | None = None, config: dict | None = None) -> None:
        super().__init__(parent)
        self.config = config if config is not None else load_im_config()
        self._filter = MessageFilter(self.config)
        self._client: OneBotClient | None = None

    def start(self) -> None:
        self._filter.rotate_buffer()
        qq_cfg = self.config.get("qq") or {}
        if not qq_cfg.get("enabled"):
            return
        self._client = OneBotClient(
            ws_url=str(qq_cfg.get("ws_url") or IM_DEFAULTS["qq"]["ws_url"]),
            on_message=self._on_message,
            on_status=lambda s, d: self.status_changed.emit(s, d),
        )
        self._client.start()

    def stop(self) -> None:
        if self._client:
            self._client.stop()
            self._client = None

    def restart(self) -> None:
        """设置变更后重载配置并重启连接。"""
        self.stop()
        self.config = load_im_config()
        self._filter = MessageFilter(self.config)
        self.start()

    # === 客户端线程回调（emit 跨线程安全） ===
    def _on_message(self, msg: IMMessage) -> None:
        if self._filter.is_duplicate(msg):
            return
        self._filter.append_buffer(msg)
        self.message_received.emit(msg)
        if self._filter.should_notify(msg):
            self.message_notified.emit(msg)
