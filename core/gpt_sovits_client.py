# core/gpt_sovits_client.py
"""GPT-SoVITS V3 少样本推理 TTS 客户端。

使用 voice_sample.mp3（15s 红莉栖干净人声）做参考音频，调用 GPT-SoVITS V3
Python API 进行少样本语音克隆推理。

使用方式：
  from core.gpt_sovits_client import KurisuTTS
  tts = KurisuTTS()  # 自动加载模型和参考音频
  audio_bytes = tts.synthesize("こんにちは")  # 返回 PCM 16-bit 单声道 wav bytes

前置条件：
  GPT-SoVITS V3 已安装到项目根目录 GPT-SoVITS/ 下。
  运行 python GPT-SoVITS/install.py 安装依赖和下载预训练模型。

数学本质：
  GPT-SoVITS = GPT（文本→语义token）+ SoVITS（语义token + 参考音频→波形）。
  少样本推理：pretrained GPT 将输入文本映射为语义 token 序列，
  SoVITS 将语义 token + 参考音频 mel 谱拼接后通过 VITS decoder 生成波形。
  参考音频提供音色（timbre）和韵律（prosody）约束，不提供内容。

形象理解：
  像给 AI 听一段红莉栖说话（voice_sample.mp3），然后让 AI 用红莉栖的声音
  念出你给的台词。模型学会了她的音色和说话习惯，但台词内容由你决定。
"""
from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Optional

import numpy as np

# === 配置 ===
ROOT = Path(__file__).resolve().parent.parent
SOVITS_DIR = ROOT / "GPT-SoVITS"
REF_AUDIO = ROOT / "resources" / "voice_sample.mp3"

# GPT-SoVITS V3 默认参数（少样本推理最佳实践）
DEFAULT_PARAMS = {
    "ref_audio_path": str(REF_AUDIO),
    "prompt_text": "",              # 参考音频对应文本（留空则用默认 ASR 推理）
    "prompt_lang": "ja",            # 日语
    "text_lang": "ja",              # 日语
    "top_k": 15,                    # GPT 采样 top-k
    "top_p": 0.6,                   # GPT 采样 top-p
    "temperature": 0.6,             # GPT 采样温度
    "speed": 1.0,                   # 语速
}


class KurisuTTS:
    """红莉栖 TTS：GPT-SoVITS V3 少样本推理。

    单例模式：模型只加载一次，避免重复加载消耗显存。
    """

    _instance: Optional[KurisuTTS] = None
    _ready: bool = False

    def __new__(cls) -> KurisuTTS:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._tts = None
        self._available = self._check_ready()

    def _check_ready(self) -> bool:
        """检查 GPT-SoVITS 是否可用。"""
        if not SOVITS_DIR.exists():
            return False
        try:
            import torch
            return True
        except ImportError:
            return False

    @property
    def available(self) -> bool:
        return self._available

    def _lazy_load(self) -> bool:
        """延迟加载 GPT-SoVITS 模型（首次调用 synthesize 时）。"""
        if self._ready:
            return True
        if not self._available:
            return False
        try:
            # GPT-SoVITS V3 推理 API
            # 路径：GPT-SoVITS/GPT_SoVITS/TTS_infer_pack/TTS.py
            import sys
            sovits_path = str(SOVITS_DIR.resolve())
            if sovits_path not in sys.path:
                sys.path.insert(0, sovits_path)

            from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config

            config = TTS_Config(str(SOVITS_DIR / "GPT_SoVITS" / "configs" / "tts_infer.yaml"))
            self._tts = TTS(config)
            self._ready = True
            return True
        except Exception as e:
            print(f"[KurisuTTS] 模型加载失败: {e}")
            return False

    def synthesize(self, text: str, ref_audio: str | None = None) -> Optional[bytes]:
        """合成语音。

        Args:
            text: 要合成的文本（日语）。
            ref_audio: 参考音频路径，默认用 voice_sample.mp3。

        Returns:
            PCM 16-bit 单声道 wav bytes，失败返回 None。
        """
        if not text.strip():
            return None

        if not self._lazy_load():
            return None

        try:
            ref_path = ref_audio or DEFAULT_PARAMS["ref_audio_path"]
            if not Path(ref_path).exists():
                print(f"[KurisuTTS] 参考音频不存在: {ref_path}")
                return None

            # GPT-SoVITS V3 API: tts.run(text, ref_audio_path, prompt_text, ...)
            # 返回 (sample_rate, audio_numpy)
            sr, audio = self._tts.run(
                text=text,
                ref_audio_path=ref_path,
                prompt_text=DEFAULT_PARAMS["prompt_text"],
                prompt_lang=DEFAULT_PARAMS["prompt_lang"],
                text_lang=DEFAULT_PARAMS["text_lang"],
                top_k=DEFAULT_PARAMS["top_k"],
                top_p=DEFAULT_PARAMS["top_p"],
                temperature=DEFAULT_PARAMS["temperature"],
                speed=DEFAULT_PARAMS["speed"],
            )

            # 转为 PCM 16-bit wav bytes
            audio_16 = np.clip(audio, -1, 1)
            pcm = (audio_16 * 32767).astype(np.int16).tobytes()

            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(pcm)
            return buf.getvalue()

        except Exception as e:
            print(f"[KurisuTTS] 合成失败: {e}")
            return None


def _mp3_to_wav(mp3_path: str) -> Optional[str]:
    """将 mp3 参考音频转为 wav（GPT-SoVITS 推荐 wav 格式）。"""
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_mp3(mp3_path)
        wav_path = mp3_path.rsplit(".", 1)[0] + "_ref.wav"
        audio = audio.set_frame_rate(16000).set_channels(1)
        audio.export(wav_path, format="wav")
        return wav_path
    except ImportError:
        print("[KurisuTTS] pydub 未安装，无法转换 mp3→wav。请手动转换或 pip install pydub")
        return None
    except Exception as e:
        print(f"[KurisuTTS] mp3→wav 转换失败: {e}")
        return None