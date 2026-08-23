"""直连共享连接池回归测试（响应提速）。

_stream_turn_direct 原先每轮用顶层 httpx.stream() 新建连接，每轮对话/agent 工具
循环每一轮都重做 TCP+TLS 握手。改为模块级共享 Client 后，本测试守护：
多次获取返回同一实例（连接真正被复用），且 keepalive 闲置期覆盖典型对话间隔。
"""
from __future__ import annotations

import core.llm.agent_client as ac


def test_direct_client_is_shared_singleton():
    """多次获取返回同一 Client 实例（跨轮次复用连接池）。"""
    assert ac._get_direct_client() is ac._get_direct_client()


def test_direct_client_is_keepalive_pooled():
    """共享 Client 启用 keepalive 连接池，闲置 60s 才回收（覆盖连续对话间隔）。"""
    assert ac._DIRECT_KEEPALIVE_EXPIRY >= 60.0
