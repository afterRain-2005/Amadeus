"""Microphone recording and OpenAI-compatible audio transcription."""
from __future__ import annotations

import base64
from io import BytesIO
import wave

import httpx
import numpy as np


def encode_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    samples = np.clip(samples, -1, 1)
    pcm = (samples * 32767).astype(np.int16).tobytes()
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


def transcribe(wav_bytes: bytes, endpoint: str, api_key: str, model: str) -> str:
    """OpenAI 兼容音频转写。只发 input_audio，不带 text 指令部分：
    小米 mimo ASR 网关明确拒绝附带 text（400 "text prompt is injected by the
    gateway"，转写提示词由网关注入），带上 text 的请求每句都 400 → 通话无声。
    """
    audio = base64.b64encode(wav_bytes).decode("ascii")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": audio, "format": "wav"}},
        ]}],
        "stream": False,
    }
    response = httpx.post(
        endpoint.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"}, json=payload, timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()
