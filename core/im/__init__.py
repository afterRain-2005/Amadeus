"""IM 消息接入子系统（QQ / 微信，PRD docs/PRD-im-message-notify.md）。

模块划分：
- models.py       统一消息模型 IMMessage + OneBot 事件解析
- onebot_client.py QQ（NapCat 等）OneBot 11 WebSocket 客户端
- filter.py       过滤 / 去重 / 免打扰 / 本地缓冲
- manager.py      IMManager：组装上述组件，Qt signal 投递到主线程
"""
from core.im.manager import IMManager
from core.im.models import IMMessage

__all__ = ["IMManager", "IMMessage"]
