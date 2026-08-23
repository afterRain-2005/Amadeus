import json
from pathlib import Path
from unittest.mock import patch

from core.voice.aliyun_tts_client import AliyunTTS


class FakeResponse:
    def __init__(self, data: bytes, status: int = 200):
        self.data = data
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.data


def test_clone_voice_payload():
    ref = Path("resources/crs_1393.wav")
    assert ref.exists()
    seen = {}

    def fake_urlopen(request, timeout=0):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(json.dumps({"output": {"voice": "voice_123"}}).encode("utf-8"))

    with patch("core.voice.aliyun_tts_client.urlopen", fake_urlopen):
        voice, fallback, reason = AliyunTTS(" key ").clone_voice(
            ref,
            preferred_name="kurisu",
            ref_text="それに、例えば、小学生の頃の自分。",
        )

    assert voice == "voice_123"
    assert fallback is False
    assert reason is None
    assert seen["headers"]["Authorization"] == "Bearer key"
    assert seen["payload"]["model"] == "qwen-voice-enrollment"
    assert seen["payload"]["input"]["preferred_name"] == "kurisu"
    assert seen["payload"]["input"]["audio"]["data"].startswith("data:audio/")
    assert seen["payload"]["input"]["text"] == "それに、例えば、小学生の頃の自分。"


def test_clone_voice_payload_without_text():
    """不传 ref_text 时 payload 不含 text 字段（与 amadeus clone route 兼容）。"""
    ref = Path("resources/crs_1393.wav")
    seen = {}

    def fake_urlopen(request, timeout=0):
        seen["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(json.dumps({"output": {"voice": "voice_123"}}).encode("utf-8"))

    with patch("core.voice.aliyun_tts_client.urlopen", fake_urlopen):
        voice, _, _ = AliyunTTS("key").clone_voice(ref, preferred_name="kurisu")

    assert voice == "voice_123"
    assert "text" not in seen["payload"]["input"]


def test_clone_voice_reports_fallback_mode():
    """fallback_mode=true 时返回降级原因（音频质量差或与文本不匹配）。"""
    ref = Path("resources/crs_1393.wav")

    def fake_urlopen(request, timeout=0):
        return FakeResponse(
            json.dumps({
                "output": {
                    "voice": "voice_fb",
                    "fallback_mode": True,
                    "fallback_reason": "no_valid_asr_segments",
                }
            }).encode("utf-8")
        )

    with patch("core.voice.aliyun_tts_client.urlopen", fake_urlopen):
        voice, fallback, reason = AliyunTTS("key").clone_voice(ref, ref_text="text")

    assert voice == "voice_fb"
    assert fallback is True
    assert reason == "no_valid_asr_segments"


def test_synthesize_downloads_audio_url():
    calls = []

    def fake_urlopen(request, timeout=0):
        calls.append(request)
        if hasattr(request, "data"):
            payload = json.loads(request.data.decode("utf-8"))
            assert payload["input"]["voice"] == "voice_123"
            assert payload["input"]["language_type"] == "Japanese"
            return FakeResponse(json.dumps({"output": {"audio": {"url": "https://oss/audio.mp3"}}}).encode("utf-8"))
        return FakeResponse(b"MP3DATA")

    with patch("core.voice.aliyun_tts_client.urlopen", fake_urlopen):
        audio = AliyunTTS("key").synthesize("こんにちは", "voice_123", text_lang="ja")

    assert audio == b"MP3DATA"
    assert len(calls) == 2


def test_synthesize_cosyvoice_payload_and_url():
    """synthesize_cosyvoice 应 POST SpeechSynthesizer endpoint，返回 OSS URL。

    与 amadeus src/app/api/tts/route.ts:227-291 payload 字段对齐：
    model=engine, input={text, voice, format:mp3, sample_rate:24000, language_hints:[ja], speech_rate}。
    """
    seen = {}

    def fake_urlopen(request, timeout=0):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(
            json.dumps({"output": {"audio": {"url": "https://oss/cosy.mp3"}}}).encode("utf-8")
        )

    with patch("core.voice.aliyun_tts_client.urlopen", fake_urlopen):
        url = AliyunTTS("key").synthesize_cosyvoice(
            "こんにちは", "voice_123", engine="cosyvoice-v3.5-flash"
        )

    assert url == "https://oss/cosy.mp3"
    # endpoint 与 amadeus src/app/api/tts/route.ts:233 对齐
    assert seen["url"].endswith("/services/audio/tts/SpeechSynthesizer")
    # payload 字段
    p = seen["payload"]
    assert p["model"] == "cosyvoice-v3.5-flash"
    assert p["input"]["text"] == "こんにちは"
    assert p["input"]["voice"] == "voice_123"
    assert p["input"]["format"] == "mp3"
    assert p["input"]["sample_rate"] == 24000
    assert p["input"]["language_hints"] == ["ja"]
    assert p["input"]["speech_rate"] == 1.0


def test_synthesize_cosyvoice_returns_empty_without_credentials():
    """api_key 或 voice_id 为空时返回空字符串，不发请求。"""
    assert AliyunTTS("").synthesize_cosyvoice("text", "voice") == ""
    assert AliyunTTS("key").synthesize_cosyvoice("text", "") == ""
    assert AliyunTTS("key").synthesize_cosyvoice("", "voice") == ""


def test_synthesize_cosyvoice_extracts_urls_array():
    """CosyVoice 长文本分片返回 urls 数组，应取首个 URL（与 amadeus route.ts:273-282 对齐）。"""
    def fake_urlopen(request, timeout=0):
        return FakeResponse(
            json.dumps({"output": {"audio": {"urls": [
                "https://oss/part1.mp3", "https://oss/part2.mp3"
            ]}}}).encode("utf-8")
        )

    with patch("core.voice.aliyun_tts_client.urlopen", fake_urlopen):
        url = AliyunTTS("key").synthesize_cosyvoice("long text", "voice")

    assert url == "https://oss/part1.mp3"
