from desktop_pet import maybe_start_gpt_sovits


def test_aliyun_provider_skips_gpt_sovits_start(monkeypatch):
    monkeypatch.setattr("core.storage.load_config", lambda: {"tts_provider": "aliyun"})

    called = False

    def spawn(*args, **kwargs):
        nonlocal called
        called = True

    assert maybe_start_gpt_sovits(spawn=spawn) is False
    assert called is False
