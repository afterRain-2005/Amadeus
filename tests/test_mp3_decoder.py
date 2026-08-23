"""Tests for core/mp3_decoder.py: _HttpStreamableSource + decode_mp3_to_wav + decode_mp3_stream."""
import sys
from unittest.mock import MagicMock, patch

# 注入 fake miniaudio 模块，避免测试环境无 miniaudio 时 import 失败
# （D:\anaconda 无 miniaudio；.venv 有 miniaudio 但 PySide6 DLL 加载失败）
# 测试用例内部用 patch("miniaudio.stream_any") 覆盖具体方法
if "miniaudio" not in sys.modules:
    _fake_miniaudio = MagicMock()
    _fake_miniaudio.SampleFormat.SIGNED16 = 1
    _fake_miniaudio.FileFormat.MP3 = 1
    sys.modules["miniaudio"] = _fake_miniaudio

from core.voice.mp3_decoder import _HttpStreamableSource, decode_mp3_to_wav, decode_mp3_stream


class FakeHttpResponse:
    """模拟 urllib response：track read/close 调用。"""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)
        self._closed = False
        self.read_calls = 0
        self.close_calls = 0

    def read(self, n: int = -1) -> bytes:
        self.read_calls += 1
        if self._closed or not self._chunks:
            return b""
        return self._chunks.pop(0)

    def close(self) -> None:
        self.close_calls += 1
        self._closed = True


def test_http_streamable_source_read_returns_data():
    """read(n) 调用 urllib response.read(n)，返回原始 bytes。"""
    fake_resp = FakeHttpResponse([b"abc", b"def"])
    with patch("urllib.request.urlopen", return_value=fake_resp):
        src = _HttpStreamableSource("https://oss/audio.mp3")
    assert src.read(3) == b"abc"
    assert src.read(3) == b"def"
    assert src.read(3) == b""  # 数据耗尽
    assert fake_resp.read_calls == 3


def test_http_streamable_source_close_releases_response():
    """close() 关闭 urllib response，再次 read 返回 b""。"""
    fake_resp = FakeHttpResponse([b"abc"])
    with patch("urllib.request.urlopen", return_value=fake_resp):
        src = _HttpStreamableSource("https://oss/audio.mp3")
    src.close()
    assert fake_resp.close_calls == 1
    # close 后 read 返回 b""
    assert src.read(3) == b""


def test_http_streamable_source_seek_returns_minus_one():
    """OSS URL 不支持 seek，返回 -1 让 miniaudio 跳过 seek。"""
    fake_resp = FakeHttpResponse([b"abc"])
    with patch("urllib.request.urlopen", return_value=fake_resp):
        src = _HttpStreamableSource("https://oss/audio.mp3")
    # origin 参数 miniaudio 传 int，但本方法忽略它
    assert src.seek(0, 0) == -1
    assert src.seek(100, 1) == -1


def test_http_streamable_source_read_swallows_exceptions():
    """read() 异常时返回 b""，不让 miniaudio 崩溃。"""
    fake_resp = FakeHttpResponse([b"abc"])
    fake_resp.read = MagicMock(side_effect=ConnectionError("reset"))
    with patch("urllib.request.urlopen", return_value=fake_resp):
        src = _HttpStreamableSource("https://oss/audio.mp3")
    # 异常被吞，返回 b""
    assert src.read(3) == b""


def test_decode_mp3_to_wav_empty_input_returns_empty():
    """空 mp3_bytes 返回 b""，不调 miniaudio。"""
    assert decode_mp3_to_wav(b"") == b""


def test_decode_mp3_stream_empty_url_yields_nothing():
    """空 URL 不调 miniaudio，直接返回（生成器无 yield）。"""
    chunks = list(decode_mp3_stream(""))
    assert chunks == []


def test_decode_mp3_stream_none_url_yields_nothing():
    """None URL 不调 miniaudio，直接返回。"""
    chunks = list(decode_mp3_stream(None))  # type: ignore[arg-type]
    assert chunks == []


def test_decode_mp3_stream_closes_source_on_completion():
    """流式解码完成后，_HttpStreamableSource 被 close（资源释放）。"""
    fake_resp = FakeHttpResponse([b"abc"])
    fake_gen = iter([b"chunk1", b"chunk2"])

    def fake_stream_any(src, **kwargs):
        # 触发 close 验证：迭代期间不 close，迭代后才 close
        assert not fake_resp.close_calls
        yield from fake_gen

    with patch("urllib.request.urlopen", return_value=fake_resp), \
         patch("miniaudio.stream_any", fake_stream_any):
        chunks = list(decode_mp3_stream("https://oss/audio.mp3"))

    assert chunks == [b"chunk1", b"chunk2"]
    # 迭代完成后 src.close() 被调用
    assert fake_resp.close_calls == 1


def test_decode_mp3_stream_closes_source_on_exception():
    """流式解码中途异常，_HttpStreamableSource 仍被 close（finally 块）。"""
    fake_resp = FakeHttpResponse([b"abc"])

    def fake_stream_any(src, **kwargs):
        yield b"chunk1"
        raise RuntimeError("decode error")

    with patch("urllib.request.urlopen", return_value=fake_resp), \
         patch("miniaudio.stream_any", fake_stream_any):
        try:
            list(decode_mp3_stream("https://oss/audio.mp3"))
        except RuntimeError:
            pass

    # finally 块确保 close 被调用
    assert fake_resp.close_calls == 1
