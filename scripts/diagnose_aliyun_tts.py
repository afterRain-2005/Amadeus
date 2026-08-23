"""阿里云 TTS 真机诊断脚本：定位 amadeus-py 听不到语音的根因。

控制变量：固定 api_key + voice_id（从 data/config.json 读），切换 engine 实测。
对照 amadeus 原项目能听到语音，证明 API key/voice_id 本身可用，差别在 engine 选择。

实验组：
1. engine=cosyvoice-v3.5-flash（amadeus-py 当前默认）+ qwen-tts-vc- 前缀 voice_id
2. engine=qwen3-tts-vc + qwen-tts-vc- 前缀 voice_id（amadeus 原项目路径）
3. engine=cosyvoice-v3.5-flash + CosyVoice 预置音色 longxiaochun（对照）

每组打印：HTTP 状态、返回 JSON 结构、OSS URL、下载大小、解码 PCM 大小。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.voice.aliyun_tts_client import AliyunTTS
from core.voice.mp3_decoder import decode_mp3_to_wav, decode_mp3_stream


def load_user_cfg() -> dict:
    cfg_path = ROOT / "data" / "config.json"
    if not cfg_path.exists():
        print(f"[FATAL] {cfg_path} 不存在")
        sys.exit(1)
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def test_engine(tts: AliyunTTS, text: str, voice_id: str, engine: str, label: str) -> None:
    print(f"\n{'='*70}\n[实验 {label}] engine={engine}, voice_id={voice_id[:60]}...\n{'='*70}")
    if engine == "qwen3-tts-vc":
        # Qwen3-TTS-VC 走 multimodal-generation/generation endpoint
        print(f"[STEP1] 调 synthesize（multimodal-generation/generation）...")
        try:
            mp3_bytes = tts.synthesize(text, voice_id, text_lang="ja", model="qwen3-tts-vc-2026-01-22")
            print(f"  mp3_bytes 长度: {len(mp3_bytes) if mp3_bytes else 'None'}")
            if not mp3_bytes:
                print(f"  [FAIL] Qwen3-TTS-VC 合成返回空")
                return
            print(f"  [OK] Qwen3-TTS-VC 合成成功")
            try:
                wav_bytes = decode_mp3_to_wav(mp3_bytes)
                print(f"  wav_bytes 长度: {len(wav_bytes) if wav_bytes else 'None'}")
            except Exception as exc:
                print(f"  [FAIL] MP3→WAV 解码失败: {exc}")
            return
        except Exception as exc:
            print(f"  [FAIL] Qwen3-TTS-VC 异常: {exc}")
            return

    # CosyVoice 路径
    print(f"[STEP1] 调 synthesize_cosyvoice（SpeechSynthesizer endpoint）...")
    try:
        url = tts.synthesize_cosyvoice(text, voice_id, engine=engine)
        print(f"  OSS URL: {url[:80] + '...' if url else '(空)'}")
        if not url:
            print(f"  [FAIL] CosyVoice 未返回 OSS URL（引擎与音色不匹配的典型现象）")
            return
        print(f"  [OK] 拿到 OSS URL")

        print(f"[STEP2] 流式下载 + 解码 MP3 → PCM chunks...")
        total = 0
        chunks = 0
        for chunk in decode_mp3_stream(url, sample_rate=24000, timeout=30):
            total += len(chunk)
            chunks += 1
            if chunks >= 3:
                break  # 验证前 3 个 chunk 即可
        print(f"  累计 PCM bytes: {total}, chunks: {chunks}")
        if total == 0:
            print(f"  [FAIL] 流式解码返回空")
        else:
            print(f"  [OK] 流式解码成功")
    except Exception as exc:
        print(f"  [FAIL] CosyVoice 异常: {exc}")


def main() -> None:
    cfg = load_user_cfg()
    aliyun_cfg = cfg.get("aliyun_tts") or {}
    api_key = str(aliyun_cfg.get("api_key", "")).strip()
    user_voice_id = str(aliyun_cfg.get("voice_id", "")).strip()
    saved_engine = str(aliyun_cfg.get("engine", "")).strip()
    print(f"[CONFIG] api_key 末4位: ***{api_key[-4:]}, 长度: {len(api_key)}")
    print(f"[CONFIG] user_voice_id: {user_voice_id}")
    print(f"[CONFIG] saved_engine: {saved_engine}")

    if not api_key or not user_voice_id:
        print("[FATAL] api_key 或 voice_id 为空")
        sys.exit(1)

    tts = AliyunTTS(api_key, timeout=30)
    text = "こんにちは、牧瀬紅莉栖です。"

    # 实验 1：amadeus-py 当前默认 engine + 用户的 Qwen3-TTS-VC 克隆音色
    test_engine(tts, text, user_voice_id, "cosyvoice-v3.5-flash", "1: cosyvoice-v3.5-flash + Qwen3 克隆音色")

    # 实验 2：amadeus 原项目路径：qwen3-tts-vc + 用户的克隆音色
    test_engine(tts, text, user_voice_id, "qwen3-tts-vc", "2: qwen3-tts-vc + Qwen3 克隆音色")

    # 实验 3：对照：cosyvoice-v3.5-flash + CosyVoice 预置音色 longxiaochun
    test_engine(tts, text, "longxiaochun", "cosyvoice-v3.5-flash", "3: cosyvoice-v3.5-flash + CosyVoice 预置音色 longxiaochun")


if __name__ == "__main__":
    main()
