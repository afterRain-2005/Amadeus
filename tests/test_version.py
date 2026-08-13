from unittest.mock import patch, MagicMock
from core.version import __version__, check_latest_version, parse_version


def test_version_is_string():
    assert isinstance(__version__, str)
    assert __version__.count(".") == 2  # 形如 0.2.0


def test_parse_version():
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("0.0.10") == (0, 0, 10)


def test_check_latest_version_no_url_returns_none():
    assert check_latest_version("") is None
    assert check_latest_version(None) is None


def test_check_latest_version_fetches_plain_text():
    fake_resp = MagicMock()
    fake_resp.read.return_value = b"0.9.0\n"
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda *a: None
    with patch("core.version.urllib.request.urlopen", return_value=fake_resp) as m:
        result = check_latest_version("https://example.com/version.txt")
    m.assert_called_once_with("https://example.com/version.txt", timeout=5)
    assert result == "0.9.0"


def test_check_latest_version_network_error_returns_none():
    with patch("core.version.urllib.request.urlopen", side_effect=OSError("timeout")):
        assert check_latest_version("https://example.com/version.txt") is None
