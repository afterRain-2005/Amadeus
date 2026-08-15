"""web_search / fetch_url 工具测试。"""
from unittest.mock import patch, MagicMock


def test_web_search_returns_formatted_results():
    from core.desktop_tools import execute_tool
    fake_results = [
        {"title": "上海天气", "body": "今天晴 28度", "href": "https://example.com/1"},
    ]
    with patch("ddgs.DDGS") as mock_ddgs:  # DDGS 在 desktop_tools 内延迟导入，mock 源模块
        instance = MagicMock()
        instance.text.return_value = fake_results
        mock_ddgs.return_value.__enter__.return_value = instance
        result = execute_tool("web_search", {"query": "上海天气"})
    assert "上海天气" in result["text"]
    assert "https://example.com/1" in result["text"]


def test_fetch_url_extracts_and_truncates():
    from core.desktop_tools import execute_tool
    with patch("core.desktop_tools.httpx_get_text") as mock_get, \
         patch("core.desktop_tools.trafilatura_extract") as mock_ext:
        mock_get.return_value = "<html>xx</html>"
        mock_ext.return_value = "正文" * 5000  # 10000 字符
        result = execute_tool("fetch_url", {"url": "https://example.com"})
    assert len(result["text"]) <= 8000
    assert "正文" in result["text"]


def test_fetch_url_rejects_non_http():
    from core.desktop_tools import execute_tool
    result = execute_tool("fetch_url", {"url": "file:///etc/passwd"})
    assert "failed" in result["text"].lower() or "不允许" in result["text"]
