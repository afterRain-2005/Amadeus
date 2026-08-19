"""route_and_send 的 companion 模式扩展测试。

验证三个新参数（system_role / skip_history / inject_system_prompt）的语义：
- system_role="companion" 时跳过 classify_input，直接走 chat 路径
- skip_history=True 时不写 conversation_history
- inject_system_prompt 注入到 messages 最前
"""
from unittest.mock import patch, MagicMock

import core.backend_router as router


def test_route_and_send_companion_skips_classify_and_history():
    """companion 模式跳过 classify_input，不写 conversation_history。"""
    captured = {}

    def fake_run_local_run(*, endpoint, api_key, model, soul_md, instructions,
                            input_text, conversation_history, memories,
                            on_status, on_delta, on_approval, on_tool_event=None,
                            max_tokens=None):
        captured["conversation_history"] = conversation_history
        captured["input_text"] = input_text
        captured["instructions"] = instructions
        return "companion reply"

    config = {"endpoint": "http://x", "api_key": "k", "model": "m"}
    history = [{"role": "user", "content": "earlier"}]

    with patch.object(router, "classify_input") as mock_classify, \
         patch("core.agent_client.run_local_run", side_effect=fake_run_local_run), \
         patch("core.hermes_launcher.ensure_gateway", return_value=False):
        reply, backend = router.route_and_send(
            config=config, input_text="主动问候文本", soul_md="SOUL",
            conversation_history=history,
            system_role="companion",
            skip_history=True,
            inject_system_prompt="PASS_THROUGH",
        )

    # classify_input 不应被调用
    mock_classify.assert_not_called()
    # conversation_history 不被改写（仍是原 list）
    assert history == [{"role": "user", "content": "earlier"}]
    # reply 透传
    assert reply == "companion reply"
    assert backend == "chat"


def test_route_and_send_default_user_mode_unchanged():
    """默认 system_role='user' 时维持现状：走 classify_input，写 history。"""
    captured = {}

    def fake_run_local_run(**kwargs):
        captured.update(kwargs)
        return "ok"

    config = {"endpoint": "http://x", "api_key": "k", "model": "m",
              "agent_router": {"mode": "auto"}}
    history = [{"role": "user", "content": "earlier"}]

    with patch.object(router, "classify_input", return_value="chat") as mock_classify, \
         patch("core.agent_client.run_local_run", side_effect=fake_run_local_run), \
         patch("core.hermes_launcher.ensure_gateway", return_value=False):
        router.route_and_send(
            config=config, input_text="hello", soul_md="SOUL",
            conversation_history=history,
        )

    mock_classify.assert_called_once()
    # user 模式下 input_text 会被附加到 history（现状行为）
    assert any("hello" in m.get("content", "") for m in history)


def test_route_and_send_inject_system_prompt_passes_through():
    """inject_system_prompt 透传到 run_local_run 的 instructions 字段。"""
    captured = {}

    def fake_run_local_run(**kwargs):
        captured.update(kwargs)
        return "ok"

    config = {"endpoint": "http://x", "api_key": "k", "model": "m"}

    with patch("core.agent_client.run_local_run", side_effect=fake_run_local_run), \
         patch("core.hermes_launcher.ensure_gateway", return_value=False):
        router.route_and_send(
            config=config, input_text="主动问候", soul_md="SOUL",
            conversation_history=[],
            system_role="companion",
            skip_history=True,
            inject_system_prompt="把下面这段用你的语气说出：",
        )

    # instructions 应包含 inject_system_prompt 内容
    assert "把下面这段用你的语气说出" in captured["instructions"]
