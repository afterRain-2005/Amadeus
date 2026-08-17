# PRD：阿里云百炼 Qwen3-TTS-VC 集成（与 GPT-SoVITS 并存）

## 1. 背景与目标

### 1.1 现状
- 桌宠 TTS 当前唯一方案是 GPT-SoVITS（[core/gpt_sovits_client.py](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/core/gpt_sovits_client.py)），要求本机或远程 GPU
- 本地 GPU（RTX 4050 6GB）勉强能跑，首句延迟 ~4s（已优化预热 + cut1）
- 远程模式（SSH 隧道）依赖网络和教育网服务器，延迟波动大
- SAPI 兜底是机械音，体验差

### 1.2 目标
- 新增阿里云百炼 Qwen3-TTS-VC 作为 TTS provider，与 GPT-SoVITS **并存**（无 auto 模式，用户手动切换）
- 云端合成，无需本地 GPU，首句延迟预期 ~2-3s（合成 1-2s + 下载 0.5-1s）
- 一键克隆红莉栖音色（参考 [D:/Desktop/Ideas/Amadeus2026/amadeus](file:///D:/Desktop/Ideas/Amadeus2026/amadeus) 项目实现）
- 设置页面可视化配置：API Key、一键克隆按钮、音色 ID 显示

### 1.3 非目标
- 不做 provider auto 模式（用户手动选 GPT-SoVITS 或阿里云）
- 不做多角色支持（当前仅红莉栖，未来扩展时再加）
- 不做流式合成（阿里云合成 API 返回 OSS URL，整体下载后播放）
- 不做 MiniMax / CosyVoice 等其他引擎（仅 Qwen3-TTS-VC）
- 不替换 GPT-SoVITS（保留作为离线/无网络场景的备选）

### 1.4 参考实现
- 参考项目：[D:/Desktop/Ideas/Amadeus2026/amadeus/src/lib/tts.ts](file:///D:/Desktop/Ideas/Amadeus2026/amadeus/src/lib/tts.ts) + [src/app/api/tts/route.ts](file:///D:/Desktop/Ideas/Amadeus2026/amadeus/src/app/api/tts/route.ts) + [src/app/api/tts/clone/route.ts](file:///D:/Desktop/Ideas/Amadeus2026/amadeus/src/app/api/tts/clone/route.ts)
- 阿里云官方文档：[Qwen3-TTS-VC](https://help.aliyun.com/zh/model-studio/developer-reference/qwen3-tts-vc) + [声音复刻](https://help.aliyun.com/zh/model-studio/developer-reference/voice-cloning)

## 2. 架构设计

### 2.1 Provider 切换架构

```
[用户设置 tts_provider]
       │
       ├── "gpt_sovits" → maybe_start_gpt_sovits + _synthesize_kurisu（现有逻辑不变）
       │
       └── "aliyun"    → 不启动本地子进程 + _synthesize_aliyun（新增）
                          │
                          ├── 一键克隆（一次性）：调阿里云 qwen-voice-enrollment API
                          └── 合成：调阿里云 qwen3-tts-vc API → mp3 → miniaudio 解码 → WAV bytes
```

**关键设计**：`_synthesize_aliyun` 内部完成 mp3 → WAV 转换，对外接口与 `_synthesize_kurisu` 完全一致（返回 `(success, wav_bytes)`）。这样 `_playback_worker` 和双缓冲流式合成架构**零改动**。

### 2.2 阿里云 API 调用流程

#### 一键克隆（一次性）
```
[用户点"一键克隆"]
    ↓
读 resources/voice_sample_clip_v2.wav → base64 data URL
    ↓
POST https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization
Body: {
  "model": "qwen-voice-enrollment",
  "input": {
    "action": "create",
    "target_model": "qwen3-tts-vc-2026-01-22",
    "preferred_name": "amadeus_kurisu",
    "audio": { "data": "data:audio/wav;base64,..." },
    "language": "ja"
  }
}
    ↓
返回 { "output": { "voice": "<音色名>" } }
    ↓
存 voice_id 到 data/config.json，标记 voice_cloned=true
```

#### 合成
```
[SpeechPlayer 调 _synthesize_aliyun(text)]
    ↓
POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
Body: {
  "model": "qwen3-tts-vc-2026-01-22",
  "input": {
    "text": "<合成文本>",
    "voice": "<克隆得到的音色名>",
    "language_type": "Japanese"
  }
}
    ↓
返回 { "output": { "audio": { "url": "<OSS URL>" } } }
    ↓
GET OSS URL → mp3 bytes
    ↓
miniaudio.decode(mp3_bytes) → PCM float32 + sample_rate
    ↓
重封装为 WAV bytes（用 wave 模块）
    ↓
返回 (True, wav_bytes)
```

### 2.3 模块划分

```
core/
├── aliyun_tts_client.py    # 新增：AliyunTTS 类（clone_voice + synthesize）
├── mp3_decoder.py          # 新增：decode_mp3 → (pcm_float32, sample_rate)
├── gpt_sovits_client.py    # 不改
└── tts_client.py           # 修改：新增 _synthesize_aliyun 方法

config.py                   # 修改：新增 ALIYUN_TTS_DEFAULTS
ui/settings_dialog.py       # 修改：新增"阿里云 TTS"tab + provider 切换
desktop_pet.py              # 修改：maybe_start_gpt_sovits 仅在 provider=gpt_sovits 时启动
```

## 3. 详细设计

### 3.1 `core/aliyun_tts_client.py`

```python
class AliyunTTS:
    CLONE_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
    SYNTH_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    TARGET_MODEL = "qwen3-tts-vc-2026-01-22"

    def __init__(self, api_key: str, timeout: float = 30.0): ...

    def clone_voice(self, ref_audio_path: Path, preferred_name: str = "amadeus_kurisu") -> str | None:
        """一键克隆音色，返回 voice_id 或 None。"""

    def synthesize(self, text: str, voice_id: str, text_lang: str = "ja") -> bytes | None:
        """合成文本，返回 mp3 bytes 或 None。"""

    @property
    def available(self) -> bool:
        """API Key 是否配置（不实际探活，避免泄露）。"""
```

### 3.2 `core/mp3_decoder.py`

```python
import miniaudio
import numpy as np
import wave
import io

def decode_mp3_to_wav(mp3_bytes: bytes) -> bytes:
    """mp3 bytes → WAV bytes（用 miniaudio 解码 + wave 重封装）。

    miniaudio.decode 返回 (pcm_int16, sample_rate, n_channels)，
    用 wave 模块封装为标准 WAV bytes，供 _play_wav 直接播放。
    """
    decoded = miniaudio.decode(mp3_bytes, output_format=miniaudio.SampleFormat.SIGNED16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(decoded.n_channels)
        wav.setsampwidth(2)  # SIGNED16 = 2 bytes
        wav.setframerate(decoded.sample_rate)
        wav.writeframes(decoded.samples.tobytes())
    return buf.getvalue()
```

### 3.3 `core/tts_client.py` 修改

```python
def _synthesize_aliyun(self, text: str, *, text_lang: str | None = None) -> tuple[bool, bytes | None]:
    """阿里云 TTS 合成，返回 (success, wav_bytes)。

    内部完成 mp3 → wav 转换，对外接口与 _synthesize_kurisu 一致。
    """
    try:
        from core.aliyun_tts_client import AliyunTTS
        from core.mp3_decoder import decode_mp3_to_wav
        from config import ALIYUN_TTS_DEFAULTS
        from core.storage import load_config

        cfg = {**ALIYUN_TTS_DEFAULTS, **(load_config().get("aliyun_tts") or {})}
        api_key = cfg.get("api_key", "")
        voice_id = cfg.get("voice_id", "")
        if not api_key or not voice_id:
            return False, None

        tts = AliyunTTS(api_key)
        mp3_bytes = tts.synthesize(text, voice_id, text_lang=(text_lang or "ja"))
        if not mp3_bytes or self._stop_event.is_set():
            return False, None
        wav_bytes = decode_mp3_to_wav(mp3_bytes)
        return True, wav_bytes
    except Exception as exc:
        print(f"[SpeechPlayer] Aliyun TTS failed: {exc}")
        return False, None

def _synthesize_and_enqueue(self, sentence: str, text_lang: str | None, is_first: bool = False) -> None:
    if self._stop_event.is_set() or not sentence:
        return
    # 根据 provider 选择合成方法
    provider = self._get_tts_provider()
    if provider == "aliyun":
        ok, wav_bytes = self._synthesize_aliyun(sentence, text_lang=text_lang)
    else:
        ok, wav_bytes = self._synthesize_kurisu(
            sentence, text_lang=text_lang, prompt_text=None, prompt_lang="ja", is_first=is_first,
        )
    if not ok or not wav_bytes:
        print(f"[SpeechPlayer] streaming sentence failed: {sentence[:30]}")
        return
    self._playback_queue.put(wav_bytes)
```

### 3.4 `config.py` 新增

```python
ALIYUN_TTS_DEFAULTS: dict[str, object] = {
    "api_key": "",                    # 阿里云百炼 API Key
    "voice_id": "",                   # 克隆得到的音色名（一键克隆后自动填）
    "voice_cloned": False,            # 是否已克隆（避免重复克隆）
    "preferred_name": "amadeus_kurisu",  # 克隆时的音色名（阿里云控制台显示用）
    "ref_audio": "/voice_sample_clip_v2.wav",  # 克隆用参考音频
}

# 顶层 TTS provider 选择
TTS_PROVIDER_DEFAULT = "gpt_sovits"  # gpt_sovits / aliyun
```

### 3.5 `ui/settings_dialog.py` 修改

#### "语音合成"tab 顶部新增 provider 选择
```python
# 在 voice_form 开头加
self.tts_provider = QComboBox()
self.tts_provider.addItem("GPT-SoVITS（本地/SSH）", "gpt_sovits")
self.tts_provider.addItem("阿里云百炼（云端）", "aliyun")
idx = self.tts_provider.findData(config.get("tts_provider", "gpt_sovits"))
self.tts_provider.setCurrentIndex(max(idx, 0))
voice_form.addRow("TTS Provider", self.tts_provider)
```

#### 新增"阿里云 TTS"tab
```python
aliyun_page = QWidget()
aliyun_form = QFormLayout(aliyun_page)
_tune_form(aliyun_form)
aliyun_form.addRow(_section("ALIYUN BAILIAN TTS"))

aliyun_cfg = {**ALIYUN_TTS_DEFAULTS, **(config.get("aliyun_tts") or {})}

self.aliyun_api_key = QLineEdit(aliyun_cfg.get("api_key", ""))
self.aliyun_api_key.setEchoMode(QLineEdit.Password)
aliyun_form.addRow("API Key", self.aliyun_api_key)

self.aliyun_voice_id = QLineEdit(aliyun_cfg.get("voice_id", ""))
self.aliyun_voice_id.setReadOnly(True)  # 克隆后自动填，用户一般不改
aliyun_form.addRow("音色 ID", self.aliyun_voice_id)

self.aliyun_clone_btn = QPushButton("一键克隆红莉栖音色")
self.aliyun_clone_btn.clicked.connect(self._on_clone_voice)
aliyun_form.addRow(self.aliyun_clone_btn)

self.aliyun_status = QLabel("未克隆")
self.aliyun_status.setStyleSheet("color:#8a7f63")
self.aliyun_status.setWordWrap(True)
aliyun_form.addRow("状态", self.aliyun_status)

tabs.addTab(_scroll_page(aliyun_page), "阿里云 TTS")
```

#### 一键克隆按钮处理
```python
def _on_clone_voice(self):
    api_key = self.aliyun_api_key.text().strip()
    if not api_key:
        self.aliyun_status.setText("请先填 API Key")
        return
    self.aliyun_clone_btn.setEnabled(False)
    self.aliyun_status.setText("克隆中...（5-15s）")

    def _clone_worker():
        try:
            from core.aliyun_tts_client import AliyunTTS
            from config import ALIYUN_TTS_DEFAULTS, RESOURCES_DIR
            tts = AliyunTTS(api_key)
            ref_audio = RESOURCES_DIR / "voice_sample_clip_v2.wav"
            voice_id = tts.clone_voice(ref_audio)
            if voice_id:
                # 更新 UI（主线程）
                QMetaObject.invokeMethod(self, "_on_clone_done", Qt.QueuedConnection,
                                          Q_ARG(str, voice_id))
            else:
                QMetaObject.invokeMethod(self, "_on_clone_failed", Qt.QueuedConnection,
                                          Q_ARG(str, "克隆失败，请检查 API Key"))
        except Exception as exc:
            QMetaObject.invokeMethod(self, "_on_clone_failed", Qt.QueuedConnection,
                                      Q_ARG(str, str(exc)))

    threading.Thread(target=_clone_worker, daemon=True).start()
```

### 3.6 `desktop_pet.py` 修改

```python
def maybe_start_gpt_sovits(spawn=subprocess.Popen) -> bool:
    """仅在 provider=gpt_sovits 时启动。"""
    try:
        from core.storage import load_config
        provider = load_config().get("tts_provider", "gpt_sovits")
        if provider == "aliyun":
            return False  # 阿里云模式不启动本地子进程
    except Exception:
        pass
    # ... 原有逻辑
```

## 4. 依赖与打包

### 4.1 新增依赖
- `miniaudio>=1.59`：pip install miniaudio，纯 Python 绑定 + 预编译 C 库
- 添加到 [requirements.txt](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/requirements.txt)

### 4.2 PyInstaller 打包
- miniaudio 是 PyInstaller 友好的（无外部 dll 依赖）
- [Amadeus.spec](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/Amadeus.spec) 不需要改动（miniaudio 的 .pyd 会被自动收集）

## 5. 测试方案

### 5.1 单元测试
- `tests/test_aliyun_tts_client.py`：
  - mock urlopen 验证 clone_voice / synthesize 的 payload 构建
  - 验证 API Key trim 处理（参考项目常见 401 元凶）
- `tests/test_mp3_decoder.py`：
  - 用真实 mp3 文件（resources/login.mp3）验证 decode_mp3_to_wav 输出
  - 验证输出 WAV 可被 wave.open 解析
  - 验证采样率/通道数正确

### 5.2 集成测试（手动）
- 用户填 API Key → 点"一键克隆" → 状态显示"克隆成功"
- 切换 provider 到"阿里云" → 发消息 → 听到红莉栖语音
- 网络中断 → 合成失败 → 不崩溃

### 5.3 边界场景
- API Key 无效（401）→ 友好错误提示
- 阿里云账号未实名（403）→ 提示用户实名
- 声音复刻服务未开通 → 提示用户开通
- 重复克隆 → 提示"已克隆，如需重新克隆请先清除标记"

## 6. 风险与限制

| 风险 | 缓解措施 |
|---|---|
| 阿里云服务可用性 | 保留 GPT-SoVITS 作为离线备选 |
| API Key 明文存储 | 沿用现有 config.json 模式（与 LLM API Key 一致），未来可考虑 keyring |
| 音色数量限制（20个/账号） | voice_cloned 标记防重复克隆 |
| 网络延迟波动 | 合成超时 30s，失败回退 SAPI（如开启 allow_fallback） |
| miniaudio PyInstaller 兼容性 | 测试打包后 exe 能正常解码 mp3 |
| OSS URL 跨区域访问慢 | 阿里云 OSS 默认同区域，国内访问 <1s |

## 7. 实施步骤

1. `pip install miniaudio` + 添加到 requirements.txt
2. 写 `core/mp3_decoder.py` + 测试（用真实 mp3 验证）
3. 写 `core/aliyun_tts_client.py` + 测试（mock urlopen）
4. 修改 `config.py` 加 ALIYUN_TTS_DEFAULTS
5. 修改 `core/tts_client.py` 加 `_synthesize_aliyun` + provider 分支
6. 修改 `ui/settings_dialog.py` 加"阿里云 TTS"tab + provider 切换
7. 修改 `desktop_pet.py` 加 provider 检查
8. 端到端测试（用户填 API Key 验证）

## 8. 用户验证清单

- [ ] `pip install miniaudio` 成功，`decode_mp3_to_wav` 能解码 resources/login.mp3
- [ ] 设置页面"阿里云 TTS"tab 显示正常
- [ ] 切换 provider 到"阿里云"后，GPT-SoVITS 不再自动启动
- [ ] 填入阿里云 API Key → 点"一键克隆" → 状态显示"克隆成功，音色 ID: xxx"
- [ ] 发消息后听到红莉栖语音（阿里云合成）
- [ ] 切换回"GPT-SoVITS" → 语音恢复本地合成
- [ ] PyInstaller 打包后 exe 能正常使用阿里云 TTS

## 9. 与 GPT-SoVITS 提速优化的关系

本次阿里云集成与 [lessons.md](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/lessons.md) 2026-08-17 的 GPT-SoVITS 提速（预热 + cut1）独立：
- GPT-SoVITS 提速：优化本地/SSH 模式的首句延迟
- 阿里云集成：新增云端 TTS provider，无需本地 GPU

两者并存，用户根据场景选择：
- 本地有 GPU + 离线需求 → GPT-SoVITS
- 无 GPU / 想要更快响应 → 阿里云
