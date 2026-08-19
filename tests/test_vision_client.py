# tests/test_vision_client.py
import base64
from io import BytesIO
from unittest.mock import patch, MagicMock

from PIL import Image

from core.vision_client import describe_screen, frame_to_data_url

def test_describe_screen_returns_text():
    """GPT-4o 视觉调用返回屏幕描述文本。"""
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "choices": [{"message": {"content": "用户在 VS Code 编辑 Python 文件"}}]
    }
    with patch("core.vision_client.httpx.post", return_value=fake_response) as mock_post:
        text = describe_screen(
            image_bytes=b"\x89PNG fake",
            endpoint="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4o",
        )
    assert "VS Code" in text
    mock_post.assert_called_once()

def test_describe_screen_failure_returns_empty():
    """视觉调用失败返回空字符串（不阻塞主管线）。"""
    with patch("core.vision_client.httpx.post", side_effect=Exception("network")):
        text = describe_screen(b"x", "https://api.openai.com/v1", "sk-test", "gpt-4o")
    assert text == ""

def test_frame_to_data_url_encodes_png():
    """mss 截帧 bytes → base64 data URL。"""
    url = frame_to_data_url(b"\x89PNG fake bytes")
    assert url.startswith("data:image/png;base64,")


def test_frame_to_data_url_uses_real_bgra_size():
    """mss BGRA bytes 应按真实宽高转成正常 PNG，而不是 1xN 长条。"""
    bgra = bytes([
        0, 0, 255, 255,
        0, 255, 0, 255,
    ])
    url = frame_to_data_url(bgra, (2, 1))
    payload = base64.b64decode(url.split(",", 1)[1])
    image = Image.open(BytesIO(payload))
    assert image.size == (2, 1)

def test_describe_screen_empty_key_returns_empty():
    """未配 key 直接返回空（降级关闭屏幕共享）。"""
    text = describe_screen(b"x", "https://api.openai.com/v1", "", "gpt-4o")
    assert text == ""
