"""语音离线自愈测试：available TTL 重查 + tts_offline 信号 + API 自启决策。"""
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_player():
    """构造测试用 SpeechPlayer，强制走 gpt_sovits provider。

    config.TTS_PROVIDER_DEFAULT 已改为 "aliyun"（默认走阿里云 TTS），
    但本测试套件 mock 的是 _check_kurisu/_speak_kurisu，需要强制 gpt_sovits
    路径才能让 _check_provider_available 调到 _check_kurisu。
    """
    from core.tts_client import SpeechPlayer
    player = SpeechPlayer()
    # 覆盖实例方法：_get_tts_provider 始终返回 "gpt_sovits"
    player._get_tts_provider = lambda: "gpt_sovits"
    return player


def test_offline_emits_signal_and_rechecks_after_ttl():
    """API 离线 → 发 tts_offline；TTL 过期后重查恢复（不再发信号）。"""
    player = _make_player()
    offline_count = {"n": 0}
    player.tts_offline.connect(lambda: offline_count.__setitem__("n", offline_count["n"] + 1))

    with patch.object(player, "_check_kurisu", side_effect=[False, True]), \
         patch.object(player, "_speak_kurisu", return_value=True) as mock_speak:
        # 第一次：离线，不发 fallback → 信号
        player._speak_worker("テスト", allow_fallback=False)
        assert offline_count["n"] == 1
        assert player._kurisu_available is False

        # TTL 内重试：不重查（_check_kurisu 仍只被调过 1 次），仍离线 → 再发信号
        player._speak_worker("テスト", allow_fallback=False)
        assert player._check_kurisu.call_count == 1
        assert offline_count["n"] == 2

        # TTL 过期：重查 → 可用 → 合成成功，不再发信号
        player._available_checked_at -= player._AVAILABLE_TTL + 1
        player._speak_worker("テスト", allow_fallback=False)
        assert offline_count["n"] == 2
        mock_speak.assert_called_once()
        assert player._kurisu_available is True


def test_synthesize_failure_flips_cache_and_emits():
    """可用但合成真实失败（非打断）→ 翻转缓存 + 发离线信号。"""
    player = _make_player()
    offline_count = {"n": 0}
    player.tts_offline.connect(lambda: offline_count.__setitem__("n", offline_count["n"] + 1))

    with patch.object(player, "_check_kurisu", return_value=True), \
         patch.object(player, "_speak_kurisu", return_value=False):
        player._speak_worker("テスト", allow_fallback=False)
        assert offline_count["n"] == 1
        assert player._kurisu_available is False  # 翻转，走 TTL 重查


def test_user_interrupt_does_not_emit_offline():
    """用户打断（stop_event 置位）不误报离线。"""
    player = _make_player()
    offline_count = {"n": 0}
    player.tts_offline.connect(lambda: offline_count.__setitem__("n", offline_count["n"] + 1))

    player._stop_event.set()  # 模拟打断
    with patch.object(player, "_check_kurisu", return_value=True), \
         patch.object(player, "_speak_kurisu", return_value=False):
        player._speak_worker("テスト", allow_fallback=False)
    assert offline_count["n"] == 0


def test_provider_failure_uses_requested_sapi_fallback_text():
    """云端合成失败时应朗读中文译文，并标记为降级而非完全离线。"""
    player = _make_player()
    degraded: list[str] = []
    offline_count = {"n": 0}
    player.tts_degraded.connect(degraded.append)
    player.tts_offline.connect(lambda: offline_count.__setitem__("n", offline_count["n"] + 1))

    with patch.object(player, "_check_kurisu", return_value=True), \
         patch.object(player, "_speak_kurisu", return_value=False), \
         patch.object(player, "_speak_sapi_blocking", return_value=True) as mock_sapi:
        player._speak_worker(
            "ええ、どうしたの？",
            text_lang="ja",
            allow_fallback=True,
            fallback_text="嗯，怎么了？",
            fallback_lang="zh",
        )

    mock_sapi.assert_called_once_with(
        "嗯，怎么了？", session_id=player._session_id, language="zh"
    )
    assert degraded
    assert offline_count["n"] == 0


def test_stream_without_audio_uses_fallback_after_end():
    """流式 TTS 一帧音频都没产生时，完整中文译文仍必须进入系统语音。"""
    player = _make_player()
    player._stream_allow_fallback = True
    player._stream_fallback_text = "云端失败后的中文回答"
    player._stream_fallback_lang = "zh"
    player._stream_queue.put(("ええ、どうしたの？", "ja"))
    player._stream_queue.put(None)
    degraded: list[str] = []
    player.tts_degraded.connect(degraded.append)

    with patch.object(player, "_check_kurisu", return_value=True), \
         patch.object(player, "_synthesize_and_enqueue", return_value=False), \
         patch.object(player, "_speak_sapi_blocking", return_value=True) as mock_sapi:
        player._stream_consumer(player._session_id)

    mock_sapi.assert_called_once_with(
        "云端失败后的中文回答", session_id=player._session_id, language="zh"
    )
    assert degraded


def test_locate_gpt_sovits_dev_root(tmp_path):
    """dev 布局：root/gpt_sovits_venv + root/GPT-SoVITS 可被定位。"""
    from desktop_pet import _locate_gpt_sovits
    (tmp_path / "gpt_sovits_venv" / "Scripts").mkdir(parents=True)
    (tmp_path / "gpt_sovits_venv" / "Scripts" / "python.exe").write_bytes(b"")
    api_dir = tmp_path / "GPT-SoVITS"
    api_dir.mkdir()
    (api_dir / "api_v2.py").write_bytes(b"")
    located = _locate_gpt_sovits(tmp_path)
    assert located is not None
    venv_python, found_api_dir = located
    assert venv_python == tmp_path / "gpt_sovits_venv" / "Scripts" / "python.exe"
    assert found_api_dir == api_dir


def test_locate_gpt_sovits_missing_returns_none(tmp_path):
    from desktop_pet import _locate_gpt_sovits
    assert _locate_gpt_sovits(tmp_path) is None


def test_maybe_start_skips_when_online():
    """API 在线 → 不启动。"""
    from desktop_pet import maybe_start_gpt_sovits
    spawn = MagicMock()
    # config.TTS_PROVIDER_DEFAULT=aliyun 时 maybe_start 直接 return False，
    # 测试需要 gpt_sovits provider 才能走到 KurisuTTS().available 检查
    with patch("core.storage.load_config", return_value={"tts_provider": "gpt_sovits"}), \
         patch("core.gpt_sovits_client.KurisuTTS") as mock_tts:
        mock_tts.return_value.available = True
        assert maybe_start_gpt_sovits(spawn=spawn) is False
        spawn.assert_not_called()


def test_maybe_start_spawns_when_offline(tmp_path):
    """API 离线 + 路径存在 → 用 venv python 后台拉起。"""
    from desktop_pet import maybe_start_gpt_sovits
    (tmp_path / "gpt_sovits_venv" / "Scripts").mkdir(parents=True)
    venv_python = tmp_path / "gpt_sovits_venv" / "Scripts" / "python.exe"
    venv_python.write_bytes(b"")
    api_dir = tmp_path / "GPT-SoVITS"
    api_dir.mkdir()
    (api_dir / "api_v2.py").write_bytes(b"")

    spawn = MagicMock()
    with patch("core.storage.load_config", return_value={"tts_provider": "gpt_sovits"}), \
         patch("core.gpt_sovits_client.KurisuTTS") as mock_tts, \
         patch("core.gpt_sovits_proc.ROOT", tmp_path):
        mock_tts.return_value.available = False
        assert maybe_start_gpt_sovits(spawn=spawn) is True
        args, kwargs = spawn.call_args
        assert args[0] == [str(venv_python), "api_v2.py"]
        assert kwargs["cwd"] == str(api_dir)
