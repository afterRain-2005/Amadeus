"""_send 即时反应测试：发送瞬间触发呼吸动画 + emotion，不等 delta。"""


def test_send_triggers_thinking_dots_immediately():
    """_send 应立即调用 _show_thinking_dots，而非设静态'让我想想…'。"""
    from desktop_pet import _decide_send_instant_action
    action = _decide_send_instant_action()
    assert action["show_thinking_dots"] is True
    assert action["emotion"] == "thinking"


def test_send_does_not_use_static_let_me_think():
    """不应再返回静态文本'让我想想…'。"""
    from desktop_pet import _decide_send_instant_action
    action = _decide_send_instant_action()
    assert action.get("static_text") != "让我想想…"
