"""_status_text 带图标分段文案测试。"""


def test_status_text_web_search_has_icon():
    from core.llm.agent_client import _status_text
    text = _status_text("web_search", {"query": "上海天气"})
    assert "🔍" in text
    assert "上海天气" in text


def test_status_text_read_file_has_icon():
    from core.llm.agent_client import _status_text
    text = _status_text("read_file", {"path": "C:/x.txt"})
    assert "📄" in text


def test_status_text_operate_gui_has_icon():
    from core.llm.agent_client import _status_text
    text = _status_text("operate_gui", {"task": "打开浏览器"})
    assert "🖱" in text or "gui" in text.lower()


def test_status_text_unknown_tool_fallback():
    from core.llm.agent_client import _status_text
    text = _status_text("unknown_tool", {})
    assert "正在" in text
