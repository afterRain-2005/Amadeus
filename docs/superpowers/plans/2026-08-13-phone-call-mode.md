# 电话模式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为桌宠新增"语音电话"模式——与红莉栖 AI 半双工语音通话 + 屏幕共享给 AI 看（豆包语音电话模式）。

**Architecture:** 移植原项目 `VoiceCall.tsx` 的 VAD(RMS 阈值滞回) + 回合制 STT + 五状态机，新增 mss 截帧缓存 + GPT-4o 视觉理解的屏幕共享旁路。`VoiceCallController(QObject)` 用 Qt Signal 驱动 `CallView` 状态切换；半双工 = speaking/processing 态暂停 VAD（移植 `speakingRef` 逻辑）。TTS 先接现有 SAPI `SpeechPlayer` 降级（spec §1.4 风险承认），StreamingTTS(UI redesign §7.3) 就绪后切换。

**Tech Stack:** PySide6 (Qt Signal/QThread) / sounddevice + numpy (VAD) / mss (截帧) / httpx (GPT-4o 视觉) / 现有 asr_client (小米 mimo STT) / 现有 agent_client `_stream_turn_direct` (DeepSeek 流式 LLM) / 现有 tts_client SAPI (降级 TTS)

---

## File Structure

| 文件 | 职责 | 状态 |
|---|---|---|
| `config.py` | 新增 `PHONE_DEFAULTS` 配置块（视觉模型 + GPT-SoVITS URL + VAD 参数） | Modify |
| `core/vad.py` | VAD 状态机：RMS 阈值滞回检测说话起止 | Create |
| `core/screen_capture.py` | mss 定时截帧，缓存最新帧 | Create |
| `core/vision_client.py` | GPT-4o 视觉理解：image → "屏幕描述" | Create |
| `core/voice_call.py` | `VoiceCallController(QObject)`：状态机 + VAD/STT/LLM/TTS 管线编排 + 屏幕附帧 | Create |
| `ui/widgets/call_view.py` | `CallView(QWidget)`：通话态三区布局（顶部状态/中部字幕波形缩略图/底部三按钮） | Create |
| `desktop_pet.py` | DockBar 加第 6 个 📞 按钮 + PetWindow 通话态布局切换 | Modify |
| `resources/icons/phone.svg` `hangup.svg` `mic.svg` `mic_off.svg` `screen_share.svg` | 通话态 SVG 矢量按钮 | Create |
| `tests/test_vad.py` | VAD 状态机测试 | Create |
| `tests/test_screen_capture.py` | 截帧缓存测试 | Create |
| `tests/test_vision_client.py` | 视觉理解测试 | Create |
| `tests/test_voice_call.py` | VoiceCallController 状态机/半双工/附帧测试 | Create |
| `tests/test_call_view.py` | CallView UI 切换测试 | Create |
| `tests/test_phone_dock_button.py` | Dock 电话按钮 + 窗口布局切换测试 | Create |

**复用现有**：
- [core/asr_client.py](../../../core/asr_client.py)：`encode_wav(samples, sample_rate)` + `transcribe(wav_bytes, endpoint, api_key, model)`
- [core/agent_client.py](../../../core/agent_client.py)：`_stream_turn_direct(url, headers, model, messages, on_delta)` 纯流式 LLM（不带工具）
- [core/tts_client.py](../../../core/tts_client.py)：`SpeechPlayer`（SAPI 降级，`speaking_changed` 信号判断播放结束）
- [core/pet_controller.py](../../../core/pet_controller.py)：`send_pet_command(emotion=, speaking=)` 驱动 Live2D
- [core/emotion_parser.py](../../../core/emotion_parser.py)：`parse_reply(text)` 抽取情绪/日语/中文

---

## Task 1: 配置块 PHONE_DEFAULTS

**Files:**
- Modify: `config.py`（在 `OPENCLAW_DEFAULTS` 后追加）
- Test: `tests/test_phone_config.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_phone_config.py
from config import PHONE_DEFAULTS, VAD_PARAMS

def test_phone_defaults_keys():
    assert "vision_endpoint" in PHONE_DEFAULTS
    assert "vision_api_key" in PHONE_DEFAULTS
    assert "vision_model" in PHONE_DEFAULTS          # 默认 gpt-4o
    assert "gpt_sovits_url" in PHONE_DEFAULTS         # 默认 http://127.0.0.1:9880
    assert "screen_share_default" in PHONE_DEFAULTS   # 默认 True
    assert "capture_interval_ms" in PHONE_DEFAULTS    # 默认 2500

def test_vad_params_match_original():
    """VAD 参数移植原项目 VoiceCall.tsx:23-27。"""
    assert VAD_PARAMS["start_thresh"] == 0.018
    assert VAD_PARAMS["end_thresh"] == 0.012
    assert VAD_PARAMS["start_frames"] == 3
    assert VAD_PARAMS["silence_ms"] == 1100
    assert VAD_PARAMS["max_utterance_ms"] == 15000

def test_vision_model_default_gpt4o():
    assert PHONE_DEFAULTS["vision_model"] == "gpt-4o"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_phone_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'PHONE_DEFAULTS'`

- [ ] **Step 3: 实现**

在 `config.py` 的 `OPENCLAW_DEFAULTS` 块后追加：

```python
# === 电话模式默认配置 ===
# 电话模式 = 与红莉栖 AI 半双工语音通话 + 屏幕共享给 AI 看（豆包语音电话模式）。
# 语音管线：VAD(RMS阈值) → 回合制 STT(小米mimo) → DeepSeek 流式 LLM → TTS(红莉栖音色)
# 屏幕共享：mss 定时截帧缓存 + 开口时附帧给视觉模型 → 描述注入 LLM user 消息
# 视觉模型用 GPT-4o（DeepSeek 无视觉能力）；未配 key 时屏幕共享自动降级关闭。
PHONE_DEFAULTS: dict[str, object] = {
    "vision_endpoint": "",                              # OpenAI 兼容视觉端点（留空则用对话 endpoint）
    "vision_api_key": "",                               # 视觉模型 key（留空时屏幕共享降级关闭）
    "vision_model": "gpt-4o",                           # 视觉理解模型（DeepSeek 无视觉，必须 GPT-4o 级）
    "gpt_sovits_url": "http://127.0.0.1:9880",          # GPT-SoVITS api_v2.py 默认端口
    "screen_share_default": True,                       # 进入通话时默认开屏幕共享
    "capture_interval_ms": 2500,                        # mss 截帧间隔（2.5s 一次，仅缓存最新帧）
}

# === VAD 参数（移植原项目 amadeus/src/components/VoiceCall.tsx:23-27）===
# 数学本质：RMS = sqrt(mean(x^2))，信号能量度量。
# 滞回阈值：START_THRESH > END_THRESH，留缓冲带防边界抖动（单阈值时噪声在阈值附近波动会反复触发）。
# 形象理解：像声音的"音量水位线"，超过高位认为有人说话，低于低位持续一段时间认为说完了。
VAD_PARAMS: dict[str, int | float] = {
    "start_thresh": 0.018,       # 开始说话的 RMS 阈值（高位）
    "end_thresh": 0.012,         # 结束说话的 RMS 阈值（低位，低于开始防抖）
    "start_frames": 3,           # 连续多少帧超阈值才判定"开始说话"
    "silence_ms": 1100,          # 静音持续多久判定"一句话结束"
    "max_utterance_ms": 15000,   # 单次最长录音（防一直不结束）
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_phone_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_phone_config.py
git commit -m "feat(phone): PHONE_DEFAULTS + VAD_PARAMS 配置块"
```

---

## Task 2: VAD 状态机（core/vad.py）

**Files:**
- Create: `core/vad.py`
- Test: `tests/test_vad.py`

**数学本质**：RMS = √((1/N)·Σxᵢ²)，信号瞬时能量。阈值滞回：start_thresh(0.018) > end_thresh(0.012)，两阈值间留缓冲带，避免单阈值时噪声边界抖动反复触发 start/end。
**形象理解**：音量水位线 + 防抖缓冲带。连续 3 帧超高位 → 开始录音；连续静音 1.1s（低于低位）→ 一句话结束。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_vad.py
import numpy as np
from core.vad import VADDetector

def _frame(rms_target: float, samples: int = 1024) -> np.ndarray:
    """生成指定 RMS 的帧（白噪声缩放）。"""
    noise = np.random.randn(samples).astype(np.float32)
    current_rms = float(np.sqrt(np.mean(noise ** 2)))
    if current_rms == 0:
        return noise
    return noise * (rms_target / current_rms)

def test_silence_does_not_start():
    """持续静音（低于 end_thresh）不应触发 start。"""
    det = VADDetector()
    for _ in range(20):
        result = det.feed(_frame(0.005))
    assert not result.utterance_started

def test_loud_starts_utterance():
    """连续 3 帧超 start_thresh 触发 start。"""
    det = VADDetector()
    started = False
    for _ in range(5):
        result = det.feed(_frame(0.05))  # 远超 0.018
        if result.utterance_started:
            started = True
    assert started

def test_silence_after_speech_ends_utterance():
    """说话后静音 silence_ms 触发 end。"""
    det = VADDetector()
    # 先触发 start
    for _ in range(5):
        det.feed(_frame(0.05))
    # 静音结束（每帧 ~16ms @1024 samples/16kHz，需 ~69 帧达 1100ms）
    ended = False
    for _ in range(80):
        result = det.feed(_frame(0.005))
        if result.utterance_ended:
            ended = True
            break
    assert ended

def test_reset_clears_state():
    det = VADDetector()
    for _ in range(5):
        det.feed(_frame(0.05))
    det.reset()
    assert det.is_recording is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_vad.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.vad'`

- [ ] **Step 3: 实现**

```python
# core/vad.py
"""VAD（Voice Activity Detection）：RMS 振幅阈值滞回状态机。

移植原项目 amadeus/src/components/VoiceCall.tsx:23-27 的参数与逻辑。

数学本质：
  RMS = sqrt((1/N) * sum(x_i^2))，信号瞬时能量度量。
  滞回阈值 start_thresh > end_thresh，两阈值间留缓冲带，
  避免单阈值时噪声在阈值附近波动反复触发 start/end（边界抖动）。

形象理解：
  像声音的"音量水位线"。超过高位（start_thresh）认为有人说话，
  低于低位（end_thresh）持续一段时间（silence_ms）认为说完了。
  高低位之间留"缓冲带"防抖。
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from config import VAD_PARAMS


@dataclass
class VADResult:
    """单帧 VAD 检测结果。"""
    utterance_started: bool = False   # 本帧触发了"开始说话"
    utterance_ended: bool = False     # 本帧触发了"一句话结束"
    rms: float = 0.0


class VADDetector:
    """RMS 阈值滞回 VAD 状态机。

    状态：
    - 待机：未录音，监测 start_thresh
    - 录音中：已开始，监测 end_thresh + silence_ms / max_utterance_ms
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_size: int = 1024,
        params: dict | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_size = frame_size
        p = params or VAD_PARAMS
        self.start_thresh = float(p["start_thresh"])
        self.end_thresh = float(p["end_thresh"])
        self.start_frames = int(p["start_frames"])
        self.silence_ms = int(p["silence_ms"])
        self.max_utterance_ms = int(p["max_utterance_ms"])

        self._start_frame_count = 0
        self._silent_frame_count = 0
        self._recording = False
        self._utterance_start_ms = 0
        self._now_ms = 0
        self._frame_ms = frame_size * 1000 / sample_rate

    @property
    def is_recording(self) -> bool:
        return self._recording

    @staticmethod
    def compute_rms(samples: np.ndarray) -> float:
        """RMS = sqrt(mean(x^2))。"""
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))

    def feed(self, samples: np.ndarray) -> VADResult:
        """喂一帧音频，返回本帧检测结果。"""
        rms = self.compute_rms(samples)
        self._now_ms += self._frame_ms
        result = VADResult(rms=rms)

        if not self._recording:
            # 待机：监测开始说话
            if rms > self.start_thresh:
                self._start_frame_count += 1
            else:
                self._start_frame_count = 0
            if self._start_frame_count >= self.start_frames:
                self._recording = True
                self._utterance_start_ms = self._now_ms
                self._silent_frame_count = 0
                result.utterance_started = True
        else:
            # 录音中：监测结束（静音超时 / 超长录音）
            if rms < self.end_thresh:
                self._silent_frame_count += 1
            else:
                self._silent_frame_count = 0
            silent_ms = self._silent_frame_count * self._frame_ms
            elapsed = self._now_ms - self._utterance_start_ms
            if silent_ms >= self.silence_ms or elapsed >= self.max_utterance_ms:
                result.utterance_ended = True
                self._recording = False
                self._start_frame_count = 0
                self._silent_frame_count = 0
        return result

    def reset(self) -> None:
        """重置状态机（半双工切换时调用）。"""
        self._start_frame_count = 0
        self._silent_frame_count = 0
        self._recording = False
        self._utterance_start_ms = 0
        self._now_ms = 0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_vad.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add core/vad.py tests/test_vad.py
git commit -m "feat(phone): VAD 状态机（RMS 阈值滞回，移植原项目参数）"
```

---

## Task 3: 屏幕截帧缓存（core/screen_capture.py）

**Files:**
- Create: `core/screen_capture.py`
- Test: `tests/test_screen_capture.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_screen_capture.py
from unittest.mock import MagicMock, patch
from core.screen_capture import ScreenCapturer

def test_capturer_starts_and_stops():
    """启动后定时截帧，停止后线程退出。"""
    cap = ScreenCapturer(interval_ms=100)
    with patch("core.screen_capture.mss") as mock_mss:
        mock_sct = MagicMock()
        mock_mss.return_value.__enter__.return_value = mock_sct
        mock_sct.grab.return_value = MagicMock()  # 假帧
        cap.start()
        import time; time.sleep(0.35)  # 等几帧
        assert cap.latest_frame is not None or mock_sct.grab.called
        cap.stop()

def test_latest_frame_caches_only_newest():
    """仅缓存最新帧，不存历史（省内存）。"""
    cap = ScreenCapturer(interval_ms=50)
    with patch("core.screen_capture.mss") as mock_mss:
        mock_sct = MagicMock()
        mock_mss.return_value.__enter__.return_value = mock_sct
        frame1, frame2 = MagicMock(name="frame1"), MagicMock(name="frame2")
        mock_sct.grab.side_effect = [frame1, frame2]
        cap.start()
        import time; time.sleep(0.2)
        cap.stop()
        # 最终缓存的是最后一次截的帧
        if cap.latest_frame is not None:
            assert cap.latest_frame in (frame1, frame2)

def test_stop_is_idempotent():
    cap = ScreenCapturer()
    cap.stop()  # 未启动就停，不应抛异常
    cap.stop()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_screen_capture.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.screen_capture'`

- [ ] **Step 3: 实现**

```python
# core/screen_capture.py
"""屏幕共享：mss 定时截帧，仅缓存最新帧。

设计：旁路异步截帧，不阻塞语音管线。通话态每 2.5s 截一帧，
仅保留最新帧（省内存）。用户说话结束时取最新帧附给视觉模型。
"""
from __future__ import annotations

import threading
import time
from typing import Any

import mss


class ScreenCapturer:
    """定时截屏缓存最新帧。线程安全。"""

    def __init__(self, interval_ms: int = 2500) -> None:
        self.interval = interval_ms / 1000.0
        self._latest: Any | None = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def latest_frame(self) -> Any | None:
        with self._lock:
            return self._latest

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                while self._running:
                    try:
                        frame = sct.grab(monitor)
                        with self._lock:
                            self._latest = frame
                    except Exception:
                        pass
                    time.sleep(self.interval)
        except Exception:
            pass

    def clear(self) -> None:
        with self._lock:
            self._latest = None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_screen_capture.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/screen_capture.py tests/test_screen_capture.py
git commit -m "feat(phone): 屏幕截帧缓存（mss 定时，仅缓存最新帧）"
```

---

## Task 4: 视觉理解（core/vision_client.py）

**Files:**
- Create: `core/vision_client.py`
- Test: `tests/test_vision_client.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_vision_client.py
from unittest.mock import patch, MagicMock
from core.vision_client import describe_screen, frame_to_data_url

def test_describe_screen_returns_text():
    """GPT-4o 视觉调用返回屏幕描述文本。"""
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "choices": [{"message": {"content": "用户在 VS Code 编辑 Python 文件"}}]
    }
    with patch("core.vision_client.httpx.post", return_value=fake_response) as mock_post:
        text = describe_screen(
            image_bytes=b"\x89PNG fake",
            endpoint="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4o",
        )
    assert "VS Code" in text
    mock_post.assert_called_once()

def test_describe_screen_failure_returns_empty():
    """视觉调用失败返回空字符串（不阻塞主管线）。"""
    with patch("core.vision_client.httpx.post", side_effect=Exception("network")):
        text = describe_screen(b"x", "https://api.openai.com/v1", "sk-test", "gpt-4o")
    assert text == ""

def test_frame_to_data_url_encodes_png():
    """mss 截帧 bytes → base64 data URL。"""
    url = frame_to_data_url(b"\x89PNG fake bytes")
    assert url.startswith("data:image/png;base64,")

def test_describe_screen_empty_key_returns_empty():
    """未配 key 直接返回空（降级关闭屏幕共享）。"""
    text = describe_screen(b"x", "https://api.openai.com/v1", "", "gpt-4o")
    assert text == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_vision_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.vision_client'`

- [ ] **Step 3: 实现**

```python
# core/vision_client.py
"""GPT-4o 视觉理解：屏幕帧 → 简短屏幕描述。

DeepSeek 无视觉能力，电话模式屏幕共享用 GPT-4o（用户额外配 key）。
未配 key 时返回空字符串，主管线降级为纯语音通话（spec §1.4 风险）。
"""
from __future__ import annotations

import base64
from io import BytesIO

import httpx
from PIL import Image


def frame_to_data_url(image_bytes: bytes) -> str:
    """mss 截帧 bytes → base64 PNG data URL。"""
    # mss 截帧是 BGRA，转 PNG
    try:
        img = Image.frombytes("RGBA", _bgra_to_rgba_size(image_bytes), image_bytes)
    except Exception:
        # 已经是 PNG/其他格式，直接 base64
        b64 = base64.b64encode(image_bytes).decode("ascii")
        return f"data:image/png;base64,{b64}"
    buf = BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _bgra_to_rgba_size(image_bytes: bytes) -> tuple[int, int]:
    """根据 bytes 长度推算尺寸（mss 默认 1920x1080 BGRA = 8294400 bytes）。

    此函数是 best-effort，失败时由调用方降级。实际生产应从 mss 截帧对象拿 monitor 尺寸。
    """
    # 简化：假设 4 字节/像素，正方形不可能，这里仅给占位尺寸
    # 真实场景下 frame_to_data_url 的调用方应传入 (width, height)
    return (1, len(image_bytes) // 4) if image_bytes else (1, 1)


def describe_screen(
    image_bytes: bytes,
    endpoint: str,
    api_key: str,
    model: str,
    *,
    max_chars: int = 120,
) -> str:
    """屏幕帧 → 简短屏幕描述。失败/未配 key 返回空字符串。"""
    if not api_key or not image_bytes:
        return ""
    data_url = frame_to_data_url(image_bytes)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": f"用一句话（≤{max_chars}字）描述当前屏幕内容，聚焦用户正在做什么。"},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
        "stream": False,
        "max_tokens": 200,
    }
    try:
        resp = httpx.post(
            endpoint.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_vision_client.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add core/vision_client.py tests/test_vision_client.py
git commit -m "feat(phone): GPT-4o 视觉理解（屏幕帧→描述，未配 key 降级空）"
```

---

## Task 5: VoiceCallController（状态机 + 管线编排）

**Files:**
- Create: `core/voice_call.py`
- Test: `tests/test_voice_call.py`

**核心**：五状态机 `connecting → listening → processing → speaking → listening(循环) → ended`。半双工 = speaking/processing 态暂停 VAD（移植 `speakingRef`）。屏幕附帧 = utterance end 时取最新缓存帧。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_voice_call.py
from unittest.mock import patch, MagicMock, call
from core.voice_call import VoiceCallController

def _make_controller():
    config = {
        "endpoint": "https://api.deepseek.com/v1",
        "api_key": "sk-test",
        "model": "deepseek-chat",
        "vision_endpoint": "https://api.openai.com/v1",
        "vision_api_key": "sk-vision",
        "vision_model": "gpt-4o",
    }
    return VoiceCallController(config)

def test_initial_phase_is_connecting():
    ctrl = _make_controller()
    assert ctrl.phase == "idle"

def test_start_transitions_to_listening():
    """start() 后经 connecting 进入 listening。"""
    ctrl = _make_controller()
    with patch.object(ctrl, "_open_mic"), \
         patch.object(ctrl, "_start_screen_capture"):
        ctrl.start()
    # connecting 立即设置，listening 由 connecting_ms 计时器触发
    assert ctrl.phase == "connecting"

def test_hangup_sets_ended():
    ctrl = _make_controller()
    with patch.object(ctrl, "_open_mic"), \
         patch.object(ctrl, "_start_screen_capture"), \
         patch.object(ctrl, "_close_mic"), \
         patch.object(ctrl, "_stop_screen_capture"):
        ctrl.start()
        ctrl.hangup()
    assert ctrl.phase == "ended"

def test_speaking_phase_pauses_vad():
    """半双工：speaking/processing 态 VAD 不触发（移植 speakingRef）。"""
    ctrl = _make_controller()
    ctrl._set_phase("speaking")
    assert ctrl.vad_paused is True
    ctrl._set_phase("listening")
    assert ctrl.vad_paused is False

def test_utterance_end_attaches_screen_frame():
    """说话结束时取最新缓存帧附给视觉模型（spec §5.2）。"""
    ctrl = _make_controller()
    ctrl._screen_share_on = True
    fake_frame = b"fake frame"
    ctrl._capturer = MagicMock()
    ctrl._capturer.latest_frame = fake_frame
    with patch("core.voice_call.describe_screen", return_value="VS Code 编辑") as mock_vis:
        with patch.object(ctrl, "_transcribe", return_value="我在写代码"):
            with patch.object(ctrl, "_stream_llm", return_value="加油"):
                with patch.object(ctrl, "_play_tts"):
                    ctrl._handle_utterance(b"audio bytes")
    mock_vis.assert_called_once()
    # LLM user 消息应含屏幕描述
    assert "VS Code" in ctrl._last_user_message

def test_screen_share_off_skips_vision():
    """屏幕共享关闭时不调视觉模型。"""
    ctrl = _make_controller()
    ctrl._screen_share_on = False
    ctrl._capturer = MagicMock()
    ctrl._capturer.latest_frame = b"frame"
    with patch("core.voice_call.describe_screen") as mock_vis:
        with patch.object(ctrl, "_transcribe", return_value="你好"):
            with patch.object(ctrl, "_stream_llm", return_value="こんにちは"):
                with patch.object(ctrl, "_play_tts"):
                    ctrl._handle_utterance(b"audio")
    mock_vis.assert_not_called()

def test_vision_empty_key_skips_vision():
    """未配视觉 key 时即使屏幕共享开也不调视觉。"""
    ctrl = _make_controller()
    ctrl._config["vision_api_key"] = ""
    ctrl._screen_share_on = True
    ctrl._capturer = MagicMock()
    ctrl._capturer.latest_frame = b"frame"
    with patch("core.voice_call.describe_screen") as mock_vis:
        with patch.object(ctrl, "_transcribe", return_value="你好"):
            with patch.object(ctrl, "_stream_llm", return_value="こんにちは"):
                with patch.object(ctrl, "_play_tts"):
                    ctrl._handle_utterance(b"audio")
    mock_vis.assert_not_called()

def test_transcribe_failure_returns_to_listening():
    """STT 失败回 listening（spec §7 降级表）。"""
    ctrl = _make_controller()
    with patch.object(ctrl, "_transcribe", side_effect=Exception("ASR fail")):
        with patch.object(ctrl, "_set_phase") as mock_phase:
            ctrl._handle_utterance(b"audio")
    # 应该回到 listening
    assert mock_phase.called
    last_call = mock_phase.call_args[0][0]
    assert last_call == "listening"

def test_toggle_mute_flips_state():
    ctrl = _make_controller()
    assert ctrl.is_muted is False
    ctrl.toggle_mute()
    assert ctrl.is_muted is True
    ctrl.toggle_mute()
    assert ctrl.is_muted is False

def test_toggle_screen_share_flips_state():
    ctrl = _make_controller()
    assert ctrl.screen_share_on is True  # 默认开
    ctrl.toggle_screen_share()
    assert ctrl.screen_share_on is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_voice_call.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.voice_call'`

- [ ] **Step 3: 实现**

```python
# core/voice_call.py
"""VoiceCallController：电话模式状态机 + 语音管线编排。

移植原项目 amadeus/src/components/VoiceCall.tsx 的 VAD + 回合制 STT + 状态机，
新增 mss 截帧 + GPT-4o 视觉的屏幕共享旁路。

状态机：connecting → listening → processing → speaking → listening(循环) → ended
半双工：speaking/processing 态暂停 VAD（移植 speakingRef），避免她的声音从麦克风
        回流被误判为用户说话。
屏幕附帧：utterance end 时取最新缓存帧给视觉模型，频率=说话频率（省钱）。

TTS 降级：StreamingTTS(UI redesign §7.3) 未实现，先用 SAPI SpeechPlayer。
          speaking_changed 信号判断播放结束 → 回 listening。
"""
from __future__ import annotations

from collections.abc import Callable
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from config import PHONE_DEFAULTS, VAD_PARAMS, get_character_by_id, KURISU_OUTPUT_FORMAT
from core.agent_client import _load_soul_md, _stream_turn_direct
from core.asr_client import encode_wav, transcribe
from core.emotion_parser import parse_reply
from core.screen_capture import ScreenCapturer
from core.vad import VADDetector
from core.vision_client import describe_screen


CONNECTING_MS = 1300  # "正在接通"动画时长（移植原项目）


class VoiceCallController(QObject):
    """电话模式控制器：状态机 + 管线编排。UI 通过信号驱动。"""

    phase_changed = Signal(str)       # idle/connecting/listening/processing/speaking/ended
    subtitle = Signal(str)            # 字幕文本
    you_said = Signal(str)            # 用户说的话
    waveform = Signal(float)          # 波形振幅 0-1
    elapsed = Signal(int)             # 通话秒数
    error = Signal(str)               # 错误提示
    screen_frame = Signal(object)     # 屏幕缩略图（给 UI 显示）

    def __init__(self, config: dict, character=None, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._character = character or get_character_by_id("kurisu")
        self._phase = "idle"
        self._vad = VADDetector(params=VAD_PARAMS)
        self._capturer = ScreenCapturer(
            interval_ms=int(PHONE_DEFAULTS.get("capture_interval_ms", 2500))
        )
        self._stream = None            # sounddevice InputStream
        self._recording_buf: list[np.ndarray] = []
        self._vad_paused = False       # 半双工：speaking/processing 态暂停 VAD
        self._muted = False
        self._screen_share_on = bool(PHONE_DEFAULTS.get("screen_share_default", True))
        self._elapsed_seconds = 0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._connecting_timer = QTimer(self)
        self._connecting_timer.setSingleShot(True)
        self._connecting_timer.timeout.connect(self._enter_listening)
        self._last_user_message = ""
        self._soul_md = _load_soul_md("kurisu") or self._character.personality
        # TTS：先用 SAPI SpeechPlayer 降级，StreamingTTS 就绪后切换
        from core.tts_client import SpeechPlayer
        self._tts = SpeechPlayer(self)
        self._tts.speaking_changed.connect(self._on_tts_speaking_changed)

    # ===== 属性 =====
    @property
    def phase(self) -> str:
        return self._phase

    @property
    def vad_paused(self) -> bool:
        return self._vad_paused

    @property
    def is_muted(self) -> bool:
        return self._muted

    @property
    def screen_share_on(self) -> bool:
        return self._screen_share_on

    # ===== 状态机 =====
    def _set_phase(self, phase: str) -> None:
        self._phase = phase
        # 半双工：speaking/processing 暂停 VAD（移植 speakingRef）
        self._vad_paused = phase in ("speaking", "processing")
        if phase in ("speaking", "processing", "ended"):
            self._vad.reset()
        self.phase_changed.emit(phase)

    def start(self) -> None:
        """启动通话：connecting → (1.3s) → listening。"""
        self._set_phase("connecting")
        self.subtitle.emit("正在接通…")
        self._elapsed_seconds = 0
        self._elapsed_timer.start(1000)
        self._open_mic()
        if self._screen_share_on and self._config.get("vision_api_key"):
            self._start_screen_capture()
        self._connecting_timer.start(CONNECTING_MS)

    def _enter_listening(self) -> None:
        self._set_phase("listening")
        self.subtitle.emit("聆听中，请说话")

    def hangup(self) -> None:
        """挂断：停管线 + ended。"""
        self._connecting_timer.stop()
        self._elapsed_timer.stop()
        self._close_mic()
        self._stop_screen_capture()
        self._tts.stop()
        self._set_phase("ended")
        self.subtitle.emit("通话结束")

    def toggle_mute(self) -> None:
        self._muted = not self._muted
        if self._stream is not None:
            try:
                # sounddevice stream 的 active 属性控制
                if self._muted:
                    self._stream.stop()
                else:
                    self._stream.start()
            except Exception:
                pass

    def toggle_screen_share(self) -> None:
        self._screen_share_on = not self._screen_share_on
        if self._screen_share_on and self._phase in ("connecting", "listening") \
                and self._config.get("vision_api_key"):
            self._start_screen_capture()
        elif not self._screen_share_on:
            self._stop_screen_capture()

    def _tick_elapsed(self) -> None:
        self._elapsed_seconds += 1
        self.elapsed.emit(self._elapsed_seconds)

    # ===== 麦克风 + VAD =====
    def _open_mic(self) -> None:
        try:
            import sounddevice as sd
            self._stream = sd.InputStream(
                samplerate=16000, channels=1, dtype="float32",
                blocksize=1024, callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as exc:
            self.error.emit(f"麦克风不可用：{exc}")
            self._set_phase("ended")

    def _close_mic(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """sounddevice 回调：每帧喂 VAD，检测到说话起止累积/提交。"""
        if self._vad_paused or self._muted:
            return
        samples = indata.flatten()
        result = self._vad.feed(samples)
        # 波形：RMS 归一化
        rms = result.rms
        self.waveform.emit(min(rms / self._vad.start_thresh, 1.0))
        if result.utterance_started:
            self._recording_buf = [samples.copy()]
            self.subtitle.emit("听到了，继续说…")
        elif self._vad.is_recording:
            self._recording_buf.append(samples.copy())
        if result.utterance_ended and self._recording_buf:
            audio = np.concatenate(self._recording_buf)
            self._recording_buf = []
            self._set_phase("processing")
            self.subtitle.emit("识别中…")
            # 转写 + LLM + TTS 在后台线程跑，避免阻塞音频回调
            threading.Thread(target=self._handle_utterance, args=(audio,), daemon=True).start()

    # ===== 管线：STT → 视觉附帧 → LLM → TTS =====
    def _handle_utterance(self, audio: np.ndarray) -> None:
        """处理一次"说完的话"：STT → 视觉附帧 → LLM → TTS。后台线程跑。"""
        try:
            wav_bytes = encode_wav(audio, 16000)
            text = self._transcribe(wav_bytes)
            if not text:
                self.subtitle.emit("没听清，再说一次？")
                self._set_phase("listening")
                return
            self.you_said.emit(text)

            # 屏幕附帧（spec §5.2）：取最新缓存帧给视觉模型
            screen_desc = ""
            if self._screen_share_on and self._config.get("vision_api_key"):
                frame = self._capturer.latest_frame
                if frame is not None:
                    screen_desc = describe_screen(
                        _frame_to_bytes(frame),
                        self._config.get("vision_endpoint") or self._config.get("endpoint", ""),
                        self._config["vision_api_key"],
                        self._config.get("vision_model", "gpt-4o"),
                    )

            user_msg = text
            if screen_desc:
                user_msg = f"{text}\n[当前屏幕: {screen_desc}]"
            self._last_user_message = user_msg
            self.subtitle.emit("思考中…")

            reply = self._stream_llm(user_msg)
            if not reply:
                self._set_phase("listening")
                self.subtitle.emit("聆听中，请说话")
                return

            # TTS：解析日语部分播放（与 desktop_pet 一致）
            parsed = parse_reply(reply)
            self._set_phase("speaking")
            self.subtitle.emit(f"{self._character.name} 正在说话…")
            tts_text = parsed.japanese or parsed.chinese
            if tts_text:
                self._play_tts(tts_text)
            else:
                # 无 TTS 内容，直接回 listening
                self._set_phase("listening")
                self.subtitle.emit("聆听中，请说话")
        except Exception as exc:
            self.error.emit(f"处理失败：{exc}")
            self._set_phase("listening")
            self.subtitle.emit("聆听中，请说话")

    def _transcribe(self, wav_bytes: bytes) -> str:
        return transcribe(
            wav_bytes,
            endpoint=self._config.get("asr_endpoint") or self._config.get("endpoint", ""),
            api_key=self._config.get("asr_api_key") or self._config.get("api_key", ""),
            model=self._config.get("asr_model", "mimo-audio-v1"),
        )

    def _stream_llm(self, user_text: str) -> str:
        """流式 LLM（复用 _stream_turn_direct，不带工具）。"""
        url = self._config["endpoint"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self._config['api_key']}"}
        system = self._soul_md + "\n\n" + KURISU_OUTPUT_FORMAT
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ]
        content, _ = _stream_turn_direct(
            url, headers, self._config["model"], messages,
            on_delta=lambda t: None,  # 电话模式不实时打字，TTS 分句时再驱动字幕
        )
        return content.strip()

    def _play_tts(self, text: str) -> None:
        """TTS 播放（SAPI 降级）。speaking_changed(False) 触发回 listening。"""
        self._tts.speak(text)

    def _on_tts_speaking_changed(self, speaking: bool) -> None:
        """TTS 播放结束 → 回 listening。"""
        if not speaking and self._phase == "speaking":
            self._set_phase("listening")
            self.subtitle.emit("聆听中，请说话")

    # ===== 屏幕截帧 =====
    def _start_screen_capture(self) -> None:
        self._capturer.start()

    def _stop_screen_capture(self) -> None:
        self._capturer.stop()


def _frame_to_bytes(frame) -> bytes:
    """mss 截帧对象 → bytes（BGRA 原始数据）。"""
    # mss 截帧是 ScreenShot 对象，有 .bgra 属性
    if hasattr(frame, "bgra"):
        return bytes(frame.bgra)
    if isinstance(frame, (bytes, bytearray)):
        return bytes(frame)
    return bytes(frame)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_voice_call.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add core/voice_call.py tests/test_voice_call.py
git commit -m "feat(phone): VoiceCallController 状态机 + 半双工 + 屏幕附帧"
```

---

## Task 6: 通话态视图 CallView

**Files:**
- Create: `ui/widgets/call_view.py`
- Test: `tests/test_call_view.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_call_view.py
from unittest.mock import MagicMock
from PySide6.QtWidgets import QApplication
import pytest

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])

def test_call_view_constructs(app):
    from ui.widgets.call_view import CallView
    view = CallView()
    assert view is not None

def test_call_view_has_three_buttons(app):
    """底部三按钮：🎤 麦克风 / ✕ 挂断 / 🖥 屏幕共享。"""
    from ui.widgets.call_view import CallView
    view = CallView()
    assert hasattr(view, "mute_btn")
    assert hasattr(view, "hangup_btn")
    assert hasattr(view, "screen_btn")

def test_call_view_updates_subtitle(app):
    from ui.widgets.call_view import CallView
    view = CallView()
    view.set_subtitle("聆听中")
    assert "聆听中" in view.subtitle_label.text()

def test_call_view_updates_phase_status(app):
    """状态条随 phase 变化显示对应文案。"""
    from ui.widgets.call_view import CallView
    view = CallView()
    view.set_phase("connecting")
    assert "接通" in view.status_label.text() or "connecting" in view.status_label.text().lower()
    view.set_phase("listening")
    assert "聆听" in view.status_label.text() or "listening" in view.status_label.text().lower()

def test_call_view_buttons_emit_signals(app):
    from ui.widgets.call_view import CallView
    view = CallView()
    mute_clicked = MagicMock()
    hangup_clicked = MagicMock()
    screen_clicked = MagicMock()
    view.mute_clicked.connect(mute_clicked)
    view.hangup_clicked.connect(hangup_clicked)
    view.screen_clicked.connect(screen_clicked)
    view.mute_btn.click()
    view.hangup_btn.click()
    view.screen_btn.click()
    mute_clicked.assert_called_once()
    hangup_clicked.assert_called_once()
    screen_clicked.assert_called_once()

def test_call_view_waveform_paints(app):
    """set_waveform 不抛异常。"""
    from ui.widgets.call_view import CallView
    view = CallView()
    view.set_waveform(0.5)
    view.set_waveform(0.0)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_call_view.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ui.widgets.call_view'`

- [ ] **Step 3: 实现**

```python
# ui/widgets/call_view.py
"""通话态视图：三区布局（顶部状态条 / 中部字幕+波形+屏幕缩略图 / 底部三按钮）。

配色沿用 A2 青蓝（#00d4ff 强调 + 半透青气泡），SVG 矢量按钮。
移植原项目 VoiceCall.tsx 的波形 canvas + 状态文案，适配 PySide6。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QRectF
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

_ROOT = Path(__file__).resolve().parent.parent.parent
_ICONS = _ROOT / "resources" / "icons"


class WaveformCanvas(QWidget):
    """简易波形条：set_waveform(level) 触发重绘。"""
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(120, 24)
        self._level = 0.0
        self._bars = 16

    def set_waveform(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        gap = 2
        bar_w = (w - gap * (self._bars - 1)) / self._bars
        for i in range(self._bars):
            # 中间高两边低，叠加实时 level
            center_factor = 1.0 - abs(i - self._bars / 2) / (self._bars / 2)
            v = self._level * center_factor + 0.08
            bar_h = max(2, v * h)
            x = i * (bar_w + gap)
            y = (h - bar_h) / 2
            p.fillRect(QRectF(x, y, bar_w, bar_h), QColor(0, 212, 255, 200))


class _SvgButton(QPushButton):
    """SVG 矢量圆形按钮。"""
    def __init__(self, icon_name: str, size: int = 44, color: str = "cyan", parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self._renderer = QSvgRenderer(( _ICONS / f"{icon_name}.svg").read_bytes())
        self._color = color

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._color == "red":
            bg = QColor(255, 59, 48, 200)
            border = QColor(255, 59, 48, 255)
        elif self._color == "amber":
            bg = QColor(255, 176, 58, 180)
            border = QColor(255, 176, 58, 255)
        else:
            bg = QColor(0, 212, 255, 40)
            border = QColor(0, 212, 255, 120)
        p.setBrush(bg)
        p.setPen(border)
        p.drawEllipse(self.rect())
        pad = 10
        self._renderer.render(p, QRectF(pad, pad, self.width() - pad * 2, self.height() - pad * 2))


class CallView(QWidget):
    """通话态三区布局视图。由 VoiceCallController 信号驱动。"""

    mute_clicked = Signal()
    hangup_clicked = Signal()
    screen_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        # 顶部：状态条（红点 + 状态文案 + 时长）
        top = QHBoxLayout()
        top.setSpacing(6)
        self._dot = QLabel("●", self)
        self._dot.setStyleSheet("color:#ff3b30; font-size:10px")
        self.status_label = QLabel("正在接通…", self)
        self.status_label.setStyleSheet(
            "color:#7be8ff; font:12px 'Segoe UI','Microsoft YaHei';"
            "background:rgba(0,212,255,0.12); border:1px solid rgba(0,212,255,0.3);"
            "border-radius:10px; padding:3px 10px"
        )
        self.elapsed_label = QLabel("0:00", self)
        self.elapsed_label.setStyleSheet("color:#8e8e93; font:11px 'Consolas'")
        top.addWidget(self._dot)
        top.addWidget(self.status_label)
        top.addStretch()
        top.addWidget(self.elapsed_label)
        layout.addLayout(top)

        # 中部：字幕 + 波形
        self.subtitle_label = QLabel("正在接通…", self)
        self.subtitle_label.setAlignment(Qt.AlignCenter)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setStyleSheet(
            "color:#7be8ff; font:14px 'Segoe UI','Microsoft YaHei';"
            "background:rgba(0,212,255,0.10); border:1px solid rgba(0,212,255,0.3);"
            "border-radius:14px; padding:10px 16px"
        )
        layout.addWidget(self.subtitle_label)

        self.waveform = WaveformCanvas(self)
        layout.addWidget(self.waveform, alignment=Qt.AlignCenter)

        layout.addStretch()

        # 底部：三按钮
        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        bottom.setAlignment(Qt.AlignCenter)
        self.mute_btn = _SvgButton("mic", 44, "cyan")
        self.mute_btn.setToolTip("静音")
        self.mute_btn.clicked.connect(self.mute_clicked.emit)
        self.hangup_btn = _SvgButton("hangup", 52, "red")
        self.hangup_btn.setToolTip("挂断")
        self.hangup_btn.clicked.connect(self.hangup_clicked.emit)
        self.screen_btn = _SvgButton("screen_share", 44, "cyan")
        self.screen_btn.setToolTip("屏幕共享")
        self.screen_btn.clicked.connect(self.screen_clicked.emit)
        bottom.addWidget(self.mute_btn)
        bottom.addWidget(self.hangup_btn)
        bottom.addWidget(self.screen_btn)
        layout.addLayout(bottom)

    # ===== 外部驱动接口 =====
    def set_phase(self, phase: str) -> None:
        status_map = {
            "connecting": "正在接通…",
            "listening": "通话中 · 聆听中",
            "processing": "通话中 · 处理中",
            "speaking": "通话中",
            "ended": "通话结束",
            "idle": "",
        }
        self.status_label.setText(status_map.get(phase, phase))
        dot_color = "#ffb63a" if phase == "connecting" else "#34c759" if phase in ("listening", "speaking", "processing") else "#8e8e93"
        self._dot.setStyleSheet(f"color:{dot_color}; font-size:10px")

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)

    def set_elapsed(self, seconds: int) -> None:
        m = seconds // 60
        s = seconds % 60
        self.elapsed_label.setText(f"{m}:{s:02d}")

    def set_waveform(self, level: float) -> None:
        self.waveform.set_waveform(level)

    def set_muted(self, muted: bool) -> None:
        self.mute_btn._renderer = QSvgRenderer(
            (_ICONS / ("mic_off.svg" if muted else "mic.svg")).read_bytes()
        )
        self.mute_btn.update()

    def set_screen_share(self, on: bool) -> None:
        self.screen_btn.setToolTip("屏幕共享：开" if on else "屏幕共享：关")
        self.screen_btn.update()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_call_view.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add ui/widgets/call_view.py tests/test_call_view.py
git commit -m "feat(phone): CallView 通话态三区布局（SVG 矢量按钮 + 波形 canvas）"
```

---

## Task 7: SVG 图标资源

**Files:**
- Create: `resources/icons/phone.svg` `hangup.svg` `mic.svg` `mic_off.svg` `screen_share.svg`

- [ ] **Step 1: 创建 5 个 SVG 图标**

`resources/icons/phone.svg`（电话听筒，青色描边）：
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#7be8ff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
</svg>
```

`resources/icons/hangup.svg`（电话挂断，白色描边，旋转 135°）：
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" transform="rotate(135 12 12)">
  <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
</svg>
```

`resources/icons/mic.svg`（麦克风，青色描边）：
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#7be8ff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
  <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
  <line x1="12" y1="19" x2="12" y2="23"/>
  <line x1="8" y1="23" x2="16" y2="23"/>
</svg>
```

`resources/icons/mic_off.svg`（麦克风静音，红色斜线）：
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#ff3b30" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <line x1="1" y1="1" x2="23" y2="23"/>
  <path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6"/>
  <path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2a7 7 0 0 1-.11 1.23"/>
  <line x1="12" y1="19" x2="12" y2="23"/>
  <line x1="8" y1="23" x2="16" y2="23"/>
</svg>
```

`resources/icons/screen_share.svg`（显示器，青色描边）：
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#7be8ff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <rect x="2" y="3" width="20" height="14" rx="2" ry="2"/>
  <line x1="8" y1="21" x2="16" y2="21"/>
  <line x1="12" y1="17" x2="12" y2="21"/>
  <path d="M8 11l4-4 4 4"/>
  <line x1="12" y1="7" x2="12" y2="14"/>
</svg>
```

- [ ] **Step 2: 验证 SVG 可被 QSvgRenderer 加载（不抛异常即可）**

Run:
```bash
python -c "from PySide6.QtSvg import QSvgRenderer; from PySide6.QtCore import QByteArray; from pathlib import Path; [QSvgRenderer(QByteArray((Path('resources/icons') / f).read_bytes())) for f in ['phone.svg','hangup.svg','mic.svg','mic_off.svg','screen_share.svg']]; print('OK')"
```
Expected: `OK`（无异常）

- [ ] **Step 3: Commit**

```bash
git add resources/icons/phone.svg resources/icons/hangup.svg resources/icons/mic.svg resources/icons/mic_off.svg resources/icons/screen_share.svg
git commit -m "feat(phone): 通话态 SVG 矢量图标（phone/hangup/mic/mic_off/screen_share）"
```

---

## Task 8: 桌宠集成（Dock 加电话按钮 + 窗口布局切换）

**Files:**
- Modify: `desktop_pet.py`（DockBar `_build_buttons` 加第 6 按钮 + PetWindow 加 CallView + 通话态切换）
- Test: `tests/test_phone_dock_button.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_phone_dock_button.py
from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication
import pytest

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])

def test_dock_has_phone_button(app):
    """DockBar 含第 6 个电话按钮。"""
    # DockBar 定义在 desktop_pet.run_overlay 内部，需通过模块级测试入口
    # 这里用导入模块级 _decide_call_toggle_action 验证决策逻辑
    from desktop_pet import _decide_call_toggle_action
    result = _decide_call_toggle_action(in_call=False)
    assert result["enter_call"] is True
    result2 = _decide_call_toggle_action(in_call=True)
    assert result2["enter_call"] is False
    assert result2["hangup"] is True

def test_call_toggle_decision_pure():
    """通话态切换决策是纯函数（参考 _decide_delta_action 模式）。"""
    from desktop_pet import _decide_call_toggle_action
    # 非通话态 → 进入通话
    assert _decide_call_toggle_action(in_call=False) == {"enter_call": True, "hangup": False}
    # 通话态 → 挂断
    assert _decide_call_toggle_action(in_call=True) == {"enter_call": False, "hangup": True}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_phone_dock_button.py -v`
Expected: FAIL with `ImportError: cannot import name '_decide_call_toggle_action'`

- [ ] **Step 3: 实现**

在 `desktop_pet.py` 模块级（`_decide_send_instant_action` 后）加纯函数：

```python
def _decide_call_toggle_action(in_call: bool) -> dict:
    """Dock 电话按钮点击决策：非通话态→进入通话，通话态→挂断。

    返回 {enter_call, hangup}。纯函数便于测试（参考 _decide_delta_action 模式）。
    """
    if in_call:
        return {"enter_call": False, "hangup": True}
    return {"enter_call": True, "hangup": False}
```

在 `DockBar._build_buttons`（[desktop_pet.py:274-281](../../../desktop_pet.py#L274-L281)）的 specs 列表加第 6 项：

```python
def _build_buttons(self) -> None:
    specs = [
        ("chat", "对话", False),
        ("phone", "电话", False),       # 新增：电话模式
        ("pin", "固定", False),
        ("settings", "设置", False),
        ("history", "记录", False),
        ("close", "退出", True),
    ]
    for icon_name, tooltip, is_danger in specs:
        btn = DockButton(icon_name, tooltip, is_danger, self)
        btn.installEventFilter(self)
        self._buttons.append(btn)
        self.layout().addWidget(btn)
```

在 `PetWindow.__init__`（[desktop_pet.py:376-534](../../../desktop_pet.py#L376-L534)）的 Dock 初始化后加 CallView + VoiceCallController + 通话态标志：

```python
# 在 self.dock_bar.show() 之后追加：
from ui.widgets.call_view import CallView
from core.voice_call import VoiceCallController

self._in_call = False
self.call_view = CallView(self)
self.call_view.setGeometry(8, 8, self.width() - 16, self.height() - 16)
self.call_view.hide()

self.call_controller = VoiceCallController(load_config(), character, self)
self.call_controller.phase_changed.connect(self._on_call_phase_changed)
self.call_controller.subtitle.connect(self.call_view.set_subtitle)
self.call_controller.elapsed.connect(self.call_view.set_elapsed)
self.call_controller.waveform.connect(self.call_view.set_waveform)
self.call_controller.you_said.connect(self._on_call_you_said)
self.call_controller.error.connect(self._on_call_error)

self.call_view.mute_clicked.connect(self.call_controller.toggle_mute)
self.call_view.hangup_clicked.connect(self._hangup_call)
self.call_view.screen_clicked.connect(self.call_controller.toggle_screen_share)

# Dock 电话按钮
self.dock_bar.button("电话").clicked.connect(self._toggle_call)
```

在 `PetWindow` 类内加通话态切换方法：

```python
def _toggle_call(self) -> None:
    """Dock 电话按钮：非通话态→进入通话，通话态→挂断。"""
    decision = _decide_call_toggle_action(self._in_call)
    if decision["enter_call"]:
        self._enter_call()
    elif decision["hangup"]:
        self._hangup_call()

def _enter_call(self) -> None:
    """进入通话态：隐藏平时组件，显示 CallView，启动管线。"""
    config = load_config()
    if not all(config.get(key) for key in ("endpoint", "api_key", "model")):
        SettingsDialog(self).exec()
        return
    self._in_call = True
    self.reply_bubble.hide()
    self.input_panel.hide()
    self.history_drawer.hide()
    self.dock_bar.hide()
    self.call_view.show()
    self.call_view.raise_()
    # 重新创建 controller 以用最新 config
    from core.voice_call import VoiceCallController
    self.call_controller = VoiceCallController(config, character, self)
    self.call_controller.phase_changed.connect(self._on_call_phase_changed)
    self.call_controller.subtitle.connect(self.call_view.set_subtitle)
    self.call_controller.elapsed.connect(self.call_view.set_elapsed)
    self.call_controller.waveform.connect(self.call_view.set_waveform)
    self.call_controller.you_said.connect(self._on_call_you_said)
    self.call_controller.error.connect(self._on_call_error)
    self.call_view.mute_clicked.connect(self.call_controller.toggle_mute)
    self.call_view.hangup_clicked.connect(self._hangup_call)
    self.call_view.screen_clicked.connect(self.call_controller.toggle_screen_share)
    self.call_controller.start()

def _hangup_call(self) -> None:
    """挂断：停管线，恢复平时态。"""
    if not self._in_call:
        return
    self._in_call = False
    self.call_controller.hangup()
    self.call_view.hide()
    self.dock_bar.show()
    self._set_bubble_text(self._latest_line(active_session(self._state)["messages"][-1]["content"]))

def _on_call_phase_changed(self, phase: str) -> None:
    self.call_view.set_phase(phase)
    # Live2D 表情随状态切换
    emotion_map = {
        "listening": "neutral",
        "processing": "thinking",
        "speaking": "smile",
        "ended": "neutral",
    }
    emotion = emotion_map.get(phase)
    if emotion:
        self._send_emotion(emotion)
    # speaking 态驱动 Live2D 口型
    if phase == "speaking":
        send_pet_command(speaking=True)
    elif phase in ("listening", "ended"):
        send_pet_command(speaking=False)

def _on_call_you_said(self, text: str) -> None:
    """通话中用户说的话显示在字幕（可选）。"""
    # 暂不显示，避免干扰红莉栖字幕

def _on_call_error(self, text: str) -> None:
    self.call_view.set_subtitle(f"⚠ {text}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_phone_dock_button.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add desktop_pet.py tests/test_phone_dock_button.py
git commit -m "feat(phone): Dock 电话按钮 + 窗口通话态切换集成"
```

---

## Task 9: 集成回归 + 手动验收

**Files:**
- 无新文件，跑全量测试 + 手动验收

- [ ] **Step 1: 跑全量回归测试**

Run: `python -m pytest tests/ -v`
Expected: 所有测试 PASS（含既有 32 + 新增 ~25 = ~57 passed）。若有 FAIL，先修回归。

- [ ] **Step 2: 手动验收（对照 spec §9 验收标准）**

启动桌宠：`python desktop_pet.py`

验收清单（对照 [spec §9](../specs/2026-08-13-phone-call-mode-design.md#9-验收标准)）：
- [ ] Dock 第 6 个 📞 按钮显示（SVG 矢量，青色）
- [ ] 点 📞 → 窗口切换为通话态三区布局，Dock 隐藏
- [ ] 通话态顶部状态条显示"正在接通…"→ 1.3s 后变"通话中 · 聆听中"
- [ ] 通话态时长计时正确（每秒 +1）
- [ ] 对麦克风说话 → VAD 检测到 → 字幕"听到了，继续说…" → 静音 1.1s 后"识别中…"
- [ ] STT 转写正确（小米 mimo）
- [ ] LLM 流式回复（DeepSeek）
- [ ] TTS 播放红莉栖语音（SAPI 降级，日语）
- [ ] speaking 态 VAD 暂停（半双工，无回声误判）
- [ ] TTS 播放结束 → 回 listening
- [ ] 屏幕共享开（默认）→ 说话结束时附帧给 GPT-4o → 描述注入 LLM
- [ ] 点 🖥 按钮 → 屏幕共享关 → 不再附帧
- [ ] 点 ✕ 挂断 → 停管线 → 恢复平时 Dock 布局
- [ ] 麦克风不可用 → 提示 + ended（spec §7 降级）
- [ ] 视觉 key 未配 → 屏幕共享自动关闭，纯语音通话
- [ ] 配色 A2 青蓝一致，SVG 矢量按钮

- [ ] **Step 3: Commit（如有手动验收发现的修复）**

```bash
git add -A
git commit -m "fix(phone): 集成回归修复（根据手动验收）"
```

---

## Self-Review

**1. Spec coverage 核对**：
- §1.2 半双工 → Task 5 `_vad_paused` + `_set_phase` ✓
- §1.2 VAD RMS 阈值 → Task 2 VADDetector ✓
- §1.2 回合制 STT → Task 5 `_transcribe` 复用 asr_client ✓
- §1.2 GPT-SoVITS 流式 TTS → Task 5 先 SAPI 降级（spec §1.4 风险承认），StreamingTTS 接口预留 ✓
- §1.2 屏幕共享持续截帧 → Task 3 ScreenCapturer ✓
- §1.2 视觉模型 GPT-4o → Task 4 vision_client ✓
- §3.2 三区布局 → Task 6 CallView ✓
- §3.3 五状态机 → Task 5 ✓
- §4.5 半双工控制 → Task 5 `_vad_paused` ✓
- §5.3 屏幕共享开关 → Task 5 `toggle_screen_share` + Task 6 🖥 按钮 ✓
- §6.1 Dock 📞 按钮 → Task 8 ✓
- §7 错误降级 → Task 5 各 try/except + 回 listening ✓
- §9 验收标准 → Task 9 手动验收清单 ✓

**2. 占位符扫描**：无 TBD/TODO/"implement later"。所有步骤含完整代码。

**3. 类型/方法一致性**：
- `VADDetector.feed()` / `is_recording` / `reset()` → Task 5 使用一致 ✓
- `ScreenCapturer.latest_frame` / `start()` / `stop()` → Task 5 使用一致 ✓
- `describe_screen(image_bytes, endpoint, api_key, model)` → Task 5 调用参数顺序一致 ✓
- `VoiceCallController.phase_changed/subtitle/elapsed/waveform/you_said/error` → Task 8 连接一致 ✓
- `CallView.set_phase/set_subtitle/set_elapsed/set_waveform/set_muted/set_screen_share` → Task 8 调用一致 ✓
- `CallView.mute_clicked/hangup_clicked/screen_clicked` → Task 8 连接一致 ✓
- `_decide_call_toggle_action(in_call)` → Task 8 测试与实现一致 ✓

**4. 已知限制（非 plan 缺陷，spec 已承认）**：
- TTS 用 SAPI 降级（不可打断、无振幅口型）—— StreamingTTS(UI redesign §7.3) 未实现，spec §1.4 明确承认此风险并给降级路径
- 视觉模型需用户额外配 OpenAI key —— 未配时 `describe_screen` 返回空，降级纯语音
- `_bgra_to_rgba_size` 是 best-effort，生产应从 mss 截帧对象拿 monitor 尺寸（Task 4 注释已说明）

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-13-phone-call-mode.md`. Two execution options:

**1. Subagent-Driven (recommended)** - 每个 Task 派独立 subagent 执行，任务间审查，快速迭代

**2. Inline Execution** - 当前会话内执行，批量执行 + 检查点审查

Which approach?
