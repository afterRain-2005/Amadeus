"""验证 KurisuTTS.synthesize 支持 text_split_method 参数覆盖。

不依赖 PySide6，独立 mock urlopen 验证 payload 构建。
"""
import json
from unittest.mock import patch


def _make_tts():
    from core.gpt_sovits_client import KurisuTTS
    return KurisuTTS()


def _fake_urlopen_factory(captured: dict):
    """构造 fake urlopen，把 request.data 写到 captured['data']。"""

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"fake_wav_bytes"

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["data"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    return fake_urlopen


def test_synthesize_with_cut1():
    """text_split_method='cut1' 应覆盖 DEFAULT_PARAMS 中的 cut5。"""
    tts = _make_tts()
    captured = {}
    # patch Path.exists 跳过 ref_audio 存在性检查
    with patch("core.gpt_sovits_client.Path.exists", return_value=True), \
         patch("core.gpt_sovits_client.urlopen", side_effect=_fake_urlopen_factory(captured)):
        wav = tts.synthesize("テスト。", text_lang="ja", text_split_method="cut1")
    assert wav == b"fake_wav_bytes"
    assert captured["data"]["text_split_method"] == "cut1"
    assert captured["data"]["text"] == "テスト。"
    assert captured["data"]["text_lang"] == "ja"


def test_synthesize_default_uses_cut5():
    """不传 text_split_method 时保持 DEFAULT_PARAMS 的 cut5。"""
    tts = _make_tts()
    captured = {}
    with patch("core.gpt_sovits_client.Path.exists", return_value=True), \
         patch("core.gpt_sovits_client.urlopen", side_effect=_fake_urlopen_factory(captured)):
        tts.synthesize("テスト。", text_lang="ja")
    assert captured["data"]["text_split_method"] == "cut5"


def test_strip_stage_directions_still_works():
    """括号情态提示词应被过滤（回归测试）。"""
    from core.gpt_sovits_client import _strip_stage_directions
    assert _strip_stage_directions("（静かに）続けて。") == "続けて。"
    assert _strip_stage_directions("(silence) test.") == "test."
    assert _strip_stage_directions("（静かに一瞬置いて）続けて。") == "続けて。"
