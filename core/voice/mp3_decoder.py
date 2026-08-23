"""Audio decoding helpers for cloud TTS providers."""
from __future__ import annotations

import io
import wave


def decode_mp3_to_wav(mp3_bytes: bytes) -> bytes:
    """Decode MP3 bytes to standard signed-16 WAV bytes."""
    if not mp3_bytes:
        return b""
    import miniaudio

    decoded = miniaudio.decode(
        mp3_bytes,
        output_format=miniaudio.SampleFormat.SIGNED16,
    )
    samples = decoded.samples
    if hasattr(samples, "tobytes"):
        sample_bytes = samples.tobytes()
    else:
        sample_bytes = bytes(samples)

    buf = io.BytesIO()
    channels = getattr(decoded, "nchannels", getattr(decoded, "n_channels", 1))
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(decoded.sample_rate)
        wav.writeframes(sample_bytes)
    return buf.getvalue()


class _HttpStreamableSource:
    """HTTP 流式数据源，包装 urllib response 供 miniaudio.stream_any 边下边解码。

    HTTP chunked transfer 自动按需读取（read(num_bytes) 时才从网络读），
    实现 OSS URL 边下边播（与 amadeus 浏览器 <audio src=url> 等价）。
    不显式继承 miniaudio.StreamableSource（鸭子类型即可，避免构造期 import 失败）。
    """

    def __init__(self, url: str, *, timeout: float = 30.0) -> None:
        from urllib.request import urlopen
        self._resp = urlopen(url, timeout=timeout)

    def read(self, num_bytes: int) -> bytes:
        # urllib response.read(n) 在 chunked transfer 下可能返回 < n 字节（按 chunk 大小），
        # miniaudio 能处理短读，无需循环累积。
        if self._resp is None:
            return b""
        try:
            return self._resp.read(num_bytes)
        except Exception:
            return b""

    def seek(self, offset: int, origin) -> int:  # noqa: ARG002
        # OSS URL 不支持 seek，返回 -1 让 miniaudio 跳过 seek
        return -1

    def close(self) -> None:
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:
                pass
            self._resp = None


def decode_mp3_stream(url: str, *, sample_rate: int = 24000, timeout: float = 30.0):
    """流式解码 mp3 URL → PCM int16 chunks 生成器。

    与 amadeus src/app/api/tts/route.ts:288-291 OSS URL 透传策略一致：
    HTTP 边下边读 → miniaudio 边解码 → yield PCM chunks（int16 bytes）。
    上层（tts_client._play_wav_stream）用 sounddevice 边写边播。

    数学本质：三段管道并行（HTTP 下载 → 解码 → 播放），总时延 ≈ max(下载, 解码, 播放) + 启动时延，
    相比串行（下载+解码+播放）省 min(下载, 解码) 时间，与 amadeus 浏览器流式播放等价。
    """
    if not url:
        return
    import miniaudio

    src = _HttpStreamableSource(url, timeout=timeout)
    try:
        gen = miniaudio.stream_any(
            src,
            source_format=miniaudio.FileFormat.MP3,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=sample_rate,
        )
        for chunk in gen:
            if hasattr(chunk, "tobytes"):
                yield chunk.tobytes()
            else:
                yield bytes(chunk)
    finally:
        src.close()
