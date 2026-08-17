"""HTTP client for Kurisu voice synthesis through GPT-SoVITS api_v2.

Run the local GPT-SoVITS server first:
    powershell -ExecutionPolicy Bypass -File scripts/start_gpt_sovits_api.ps1

红莉栖声线配置要点：
  - REF_AUDIO: voice_sample_clip_v2.wav（从 voice_sample.mp3 截取 7-13s，6 秒片段，
    F0 260-280Hz 稳定，红莉栖声线特征最明显；原 mp3 15.3s 超出 GPT-SoVITS 3-10s 限制）
  - prompt_text: ASR(SenseVoiceSmall) 识别 clip_v2.wav 得到的对应日语文本，
    用于 GPT-SoVITS 声线克隆对齐
  - prompt_lang/text_lang=ja：红莉栖讲日语（需 py311 venv + pyopenjtalk）
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import PHONE_DEFAULTS


ROOT = Path(__file__).resolve().parent.parent
# 截取后的 6 秒片段（7-13s），红莉栖声线特征最明显
REF_AUDIO = ROOT / "resources" / "voice_sample_clip_v2.wav"
DEFAULT_BASE_URL = str(PHONE_DEFAULTS.get("gpt_sovits_url", "http://127.0.0.1:9880"))


DEFAULT_PARAMS: dict[str, object] = {
    "ref_audio_path": str(REF_AUDIO),
    "aux_ref_audio_paths": [],
    # prompt_text 由 ASR(SenseVoiceSmall) 识别 voice_sample_clip_v2.wav 得到，
    # 对应片段 7-13s 的日语文本，用于 GPT-SoVITS 声线克隆对齐
    "prompt_text": "技術的及びデータセットの制限により現在成熟していません",
    # prompt_lang=ja：红莉栖讲日语（gpt_sovits_venv py3.13 + pyopenjtalk-plus）
    "prompt_lang": "ja",
    "text_lang": "ja",
    # 声线稳定性：top_p/temperature 越低声线越像参考音频，
    # 原值 0.8/0.8 声线漂移明显（生成「聞いてるわ」时偏离红莉栖音色）
    "top_k": 15,
    "top_p": 0.6,
    "temperature": 0.6,
    # cut5=按标点切分，配合 batch_size=5 批量并行推理
    # （cut1 凑四句不切，单段853 token推理30秒；cut5 切4段但 batch_size=1 串行16秒）
    "text_split_method": "cut5",
    # batch_size=5：多段批量推理，RTX 4050 6GB 显存可承受
    "batch_size": 5,
    "batch_threshold": 0.75,
    "split_bucket": True,
    "speed_factor": 1.0,
    "fragment_interval": 0.3,
    "seed": -1,
    "media_type": "wav",
    "parallel_infer": True,
    "repetition_penalty": 1.35,
    "sample_steps": 32,
    "super_sampling": False,
    # streaming_mode=False：流式返回 chunked WAV，_play_wav 按完整 WAV 处理无法播放
    # 改用 cut1 减少切分 + 括号过滤降低合成量，首句延迟已大幅降低
    "streaming_mode": False,
}


# 过滤括号内的情态提示词（如「（静かに一瞬置いて）続けて。」→「続けて。」）
# GPT-4o 输出常含中文/日文括号的舞台指示，不应被 TTS 合成
_PAREN_RE = re.compile(r"[（(][^（）()]*[）)]")


def _strip_stage_directions(text: str) -> str:
    """Remove parenthetical stage directions from text before synthesis."""
    cleaned = _PAREN_RE.sub("", text or "").strip()
    # 清理残留的多余空格和重复标点
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def infer_text_lang(text: str) -> str:
    """Infer a GPT-SoVITS language tag from text content."""
    sample = (text or "").strip()
    if not sample:
        return str(DEFAULT_PARAMS["text_lang"])
    # 含平假名/片假名 → ja
    if any(("\u3040" <= ch <= "\u30ff") or ("\u31f0" <= ch <= "\u31ff") for ch in sample):
        return "ja"
    # 含汉字（无假名）→ zh
    if any("\u4e00" <= ch <= "\u9fff" for ch in sample):
        return "zh"
    return str(DEFAULT_PARAMS["text_lang"])


class KurisuTTS:
    """Small GPT-SoVITS HTTP wrapper returning wav bytes."""

    def __init__(
        self,
        base_url: str | None = None,
        ref_audio_path: str | Path | None = None,
        timeout: float = 90.0,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.ref_audio_path = Path(ref_audio_path or REF_AUDIO)
        self.timeout = timeout

    @property
    def available(self) -> bool:
        if not self.ref_audio_path.exists():
            return False
        try:
            with urlopen(f"{self.base_url}/docs", timeout=1.0) as response:
                return response.status < 500
        except (HTTPError, URLError, TimeoutError, OSError):
            return False

    def synthesize(
        self,
        text: str,
        ref_audio: str | Path | None = None,
        *,
        text_lang: str | None = None,
        prompt_text: str | None = None,
        prompt_lang: str | None = None,
        allow_fallback: bool = False,
        text_split_method: str | None = None,
    ) -> Optional[bytes]:
        """Synthesize text and return wav bytes, or None on failure."""
        text = _strip_stage_directions((text or ""))
        if not text:
            return None

        ref_path = Path(ref_audio or self.ref_audio_path)
        if not ref_path.exists():
            print(f"[KurisuTTS] reference audio not found: {ref_path}")
            return None

        payload = dict(DEFAULT_PARAMS)
        payload.update(
            {
                "text": text,
                "ref_audio_path": str(ref_path),
                "text_lang": (text_lang or infer_text_lang(text) or str(payload["text_lang"])).lower(),
                "prompt_lang": (prompt_lang or str(payload["prompt_lang"])).lower(),
            }
        )
        if prompt_text is not None:
            payload["prompt_text"] = prompt_text
        # 首句优化：cut1=不切分整段推理，跳过 batch 调度开销。
        # 首句已合并到 ~14 字短句，cut5 切分后段数 ≤2 用不上 batch_size=5，
        # cut1 单段推理更快（lessons 2026-08-17 提速优化）。
        if text_split_method is not None:
            payload["text_split_method"] = text_split_method

        try:
            request = Request(
                f"{self.base_url}/tts",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=self.timeout) as response:
                content = response.read()
            if not content:
                return None
            return content
        except HTTPError as exc:
            detail = exc.read(300).decode("utf-8", errors="ignore")
            print(f"[KurisuTTS] GPT-SoVITS rejected request: {detail}")
            return None
        except (URLError, TimeoutError, OSError) as exc:
            print(f"[KurisuTTS] GPT-SoVITS unavailable: {exc}")
            return None
