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
    audio = base64.b64encode(wav_bytes).decode("ascii")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": audio, "format": "wav"}},
            {"type": "text", "text": "请准确转写这段语音，只输出转写文字。"},
        ]}],
        "stream": False,
    }
    response = httpx.post(
        endpoint.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"}, json=payload, timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()
