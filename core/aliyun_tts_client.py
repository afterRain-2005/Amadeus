"""Aliyun Bailian Qwen TTS client."""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AliyunTTS:
    """Small HTTP wrapper for Qwen3-TTS-VC voice clone and synthesis."""

    CLONE_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
    SYNTH_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    # CosyVoice 合成 endpoint（与 amadeus src/app/api/tts/route.ts:233 对齐）
    COSYVOICE_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
    TARGET_MODEL = "qwen3-tts-vc-2026-01-22"

    def __init__(self, api_key: str, timeout: float = 30.0) -> None:
        self.api_key = (api_key or "").strip()
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def clone_voice(
        self,
        ref_audio_path: Path,
        preferred_name: str = "amadeus_kurisu",
        *,
        language: str = "ja",
        target_model: str | None = None,
        ref_text: str | None = None,
    ) -> tuple[str | None, bool, str | None]:
        """Create a cloned voice.

        Returns (voice_id, fallback_mode, fallback_reason). fallback_mode=True
        means Aliyun degraded the clone (audio quality or audio/text mismatch),
        per 声音复刻 HTTP API 参考 (qwen-voice-enrollment). ref_text aligns the
        clone with the reference audio text and improves clone quality.
        """
        if not self.api_key:
            return None, False, None
        ref_audio_path = Path(ref_audio_path)
        if not ref_audio_path.exists():
            raise FileNotFoundError(str(ref_audio_path))
        mime = mimetypes.guess_type(str(ref_audio_path))[0] or "audio/wav"
        data_url = "data:{};base64,{}".format(
            mime,
            base64.b64encode(ref_audio_path.read_bytes()).decode("ascii"),
        )
        input_params = {
            "action": "create",
            "target_model": target_model or self.TARGET_MODEL,
            "preferred_name": preferred_name,
            "audio": {"data": data_url},
            "language": language,
        }
        if ref_text and ref_text.strip():
            input_params["text"] = ref_text.strip()
        payload = {"model": "qwen-voice-enrollment", "input": input_params}
        response = self._post_json(self.CLONE_URL, payload)
        output = response.get("output") if isinstance(response, dict) else None
        if not isinstance(output, dict):
            return None, False, None
        voice = output.get("voice") or output.get("voice_id")
        fallback = bool(output.get("fallback_mode")) or False
        reason = str(output.get("fallback_reason") or "").strip() or None
        return (str(voice).strip() if voice else None), fallback, reason

    def synthesize(
        self,
        text: str,
        voice_id: str,
        *,
        text_lang: str = "ja",
        model: str | None = None,
    ) -> bytes | None:
        """Synthesize text and return audio bytes downloaded from Aliyun's OSS URL."""
        text = (text or "").strip()
        voice_id = (voice_id or "").strip()
        if not self.api_key or not text or not voice_id:
            return None
        payload = {
            "model": model or self.TARGET_MODEL,
            "input": {
                "text": text,
                "voice": voice_id,
                "language_type": self._language_type(text_lang),
            },
        }
        response = self._post_json(self.SYNTH_URL, payload)
        url = self._extract_audio_url(response)
        if not url:
            return None
        return self._get_bytes(url)

    def synthesize_cosyvoice(
        self,
        text: str,
        voice_id: str,
        *,
        engine: str = "cosyvoice-v3.5-flash",
        speech_rate: float = 1.0,
    ) -> str:
        """CosyVoice 合成，返回 OSS URL（不在 client 层下载，由上层流式管道边下边播）。

        与 amadeus src/app/api/tts/route.ts:227-291 完全对齐：
        - POST SpeechSynthesizer endpoint
        - body = {model: engine, input: {text, voice, format:mp3, sample_rate:24000, language_hints:[ja], speech_rate}}
        - 单分片直接返回 OSS URL 透传给播放层（省 5-8s 下载+解码时间）

        多分片场景（长文本）目前只取首个 URL，与 amadeus 单分片透传策略一致；
        长文本降级为多段合成是后续优化点。
        """
        text = (text or "").strip()
        voice_id = (voice_id or "").strip()
        if not self.api_key or not text or not voice_id:
            return ""
        if not engine:
            engine = "cosyvoice-v3.5-flash"
        payload = {
            "model": engine,
            "input": {
                "text": text,
                "voice": voice_id,
                "format": "mp3",
                "sample_rate": 24000,
                "language_hints": ["ja"],
                "speech_rate": speech_rate,
            },
        }
        response = self._post_json(self.COSYVOICE_URL, payload)
        # CosyVoice 可能返回单 url 或 urls 数组（长文本分片），与 amadeus route.ts:273-282 对齐
        output = response.get("output") if isinstance(response, dict) else None
        if not isinstance(output, dict):
            return ""
        audio = output.get("audio")
        if isinstance(audio, dict):
            url = str(audio.get("url") or "").strip()
            if url:
                return url
            urls = audio.get("urls")
            if isinstance(urls, list) and urls:
                return str(urls[0]).strip()
        elif isinstance(audio, str) and audio:
            return audio.strip()
        return ""

    def _post_json(self, url: str, payload: dict) -> dict:
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except HTTPError as exc:
            detail = exc.read(800).decode("utf-8", errors="replace")
            raise RuntimeError(f"Aliyun TTS HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"Aliyun TTS unavailable: {exc}") from exc

    def _get_bytes(self, url: str) -> bytes:
        try:
            with urlopen(url, timeout=self.timeout) as response:
                return response.read()
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"Aliyun audio download failed: {exc}") from exc

    @staticmethod
    def _language_type(text_lang: str) -> str:
        lang = (text_lang or "").lower()
        if lang.startswith("ja"):
            return "Japanese"
        if lang.startswith("zh"):
            return "Chinese"
        if lang.startswith("en"):
            return "English"
        return "Japanese"

    @staticmethod
    def _extract_audio_url(response: dict) -> str:
        output = response.get("output") if isinstance(response, dict) else None
        if not isinstance(output, dict):
            return ""
        audio = output.get("audio")
        if isinstance(audio, dict):
            return str(audio.get("url") or audio.get("audio_url") or "").strip()
        if isinstance(audio, str):
            return audio.strip()
        return str(output.get("audio_url") or output.get("url") or "").strip()
