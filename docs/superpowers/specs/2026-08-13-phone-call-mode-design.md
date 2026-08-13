# 电话模式 设计文档

> 日期：2026-08-13
> 状态：设计已与用户逐段确认，待用户最终审阅
> 定位：为桌宠新增"语音电话"模式——与红莉栖 AI 实时语音通话 + 屏幕共享给 AI 看（豆包语音电话模式）
> 上游：[2026-08-13-amadeus-productization-design.md](2026-08-13-amadeus-productization-design.md)（产品化总 spec）
> 参考：原项目 `amadeus/src/components/VoiceCall.tsx`（半双工语音通话已验证实现）

---

## 1. 背景与目标

### 1.1 用户需求

模仿字节豆包的语音电话功能：与红莉栖 AI 实时语音通话，通话中可共享屏幕让 AI 看到屏幕、基于屏幕内容对话/指导。

### 1.2 核心决策（逐段确认）

| 决策项 | 选定方案 | 依据 |
|---|---|---|
| 通话对象 | 和红莉栖 AI 通话 + 屏幕共享给 AI 看 | 豆包模式，用户确认 |
| 实时性 | **半双工**（她说完你才能说） | 原项目 VoiceCall 已验证；真全双工有回声误判难点，用户选半双工 |
| 语音引擎 | STT→DeepSeek→GPT-SoVITS 管线 | 保红莉栖音色（GPT-SoVITS），复用现有 DeepSeek key |
| VAD | 纯 RMS 振幅阈值 | 原项目方案，轻量无依赖；silero-vad 过度 |
| STT | **回合制**（VAD 判结束→整段转写） | 原项目方案，复用 asr_client.py 小米 mimo，无需流式 STT |
| TTS | GPT-SoVITS 流式（红莉栖音色） | 用户选定，本地推理，与 UI redesign spec 一致 |
| 屏幕共享 | 持续截帧缓存 + 开口时附帧给视觉模型 | 持续共享（定期截帧），视觉调用频率=说话频率（省钱） |
| 视觉模型 | GPT-4o | 用户 CUA 同款，DeepSeek 无视觉 |
| 通话 UI | A 桌宠窗口通话态 | 复用现有透明悬浮窗，与桌宠统一 |

### 1.3 不做（YAGNI）

- ❌ 真全双工可打断（回声误判难点，半双工已足够；原项目验证）
- ❌ 流式 STT（回合制 + VAD 体验已足够，原项目验证）
- ❌ 端到端实时语音 API（音色非红莉栖，与沉浸感冲突）
- ❌ 全屏通话 / 独立通话窗口（A 桌宠窗口通话态已选定）
- ❌ 真人通话（单人桌宠无另一方）

### 1.4 待定 / 风险

- **GPT-SoVITS 部署**：预训练模型 ~1.5GB，需一键安装脚本；首句推理 1-3s 延迟需"思考中"过渡掩盖（UI redesign spec 已规划）
- **StreamingTTS 前置依赖**：本设计沿用 UI redesign spec §7 规划的 `StreamingTTS` 类（GPT-SoVITS 流式 + 振幅口型 + 可打断），该类**尚未实现**。电话模式的 TTS 依赖它先落地；若并行开发，电话模式 TTS 部分先接 SAPI 降级，StreamingTTS 就绪后切换
- **视觉模型 key**：DeepSeek 无视觉，需用户额外配 OpenAI GPT-4o key；未配时屏幕共享降级关闭
- **半双工体感**：不能插话，需等红莉栖说完。通话感稍弱于真电话，但无回声问题、最稳

---

## 2. 整体架构

通话模式启动后，两条管线协同（半双工 = 输入与输出串行，共享状态机）：

```
┌─ 语音对话管线（半双工）─────────────────────────────────┐
│ 麦克风 → VAD(RMS阈值) ──检测到说话──→ MediaRecorder 整段录 │
│              ↓静音1.1s判结束                            │
│         整段 wav → asr_client.transcribe(小米mimo)       │
│              ↓转写文本                                  │
│         + 屏幕最新帧描述（见下）→ 拼进 user 消息          │
│              ↓                                          │
│         DeepSeek 流式 LLM → 流式 token                  │
│              ↓                                          │
│         GPT-SoVITS 流式 TTS（红莉栖音色）→ 分句播放      │
│              ↓                                          │
│         扬声器 + 振幅→口型 + 字幕淡入                    │
│              ↓播放结束                                  │
│         回到 listening（听麦）                          │
└────────────────────────────────────────────────────────┘

┌─ 屏幕共享管线（异步旁路）──────────────────────────────┐
│ mss 定时截帧(每2-3s) → 缓存最新帧                       │
│              ↓用户说话结束(转写前)                       │
│         取最新帧 → GPT-4o 视觉 → "当前屏幕描述"          │
│              ↓                                          │
│         拼进 DeepSeek user 消息（"当前屏幕: <描述>"）    │
└────────────────────────────────────────────────────────┘
```

**形象理解**：像打电话时你说完一句话，红莉栖"瞄一眼"你屏幕再回话——半双工意味着你们轮流说，不会抢话。

**与原项目 VoiceCall 的关系**：架构直接移植原项目的 VAD+回合制STT+状态机，增强点为①GPT-SoVITS 替代阿里云 CosyVoice ②新增屏幕共享视觉管线 ③适配 PySide6 桌宠窗口（原项目是 Web 顶部浮条）。

---

## 3. 通话 UI（形态 A：桌宠窗口通话态）

### 3.1 窗口

- 复用现有桌宠窗口 400×680 透明悬浮
- 平时态 ↔ 通话态切换：点 Dock 📞 按钮 → 窗口布局切换 + 启动音频管线；挂断 → 停管线 + 恢复 Dock

### 3.2 三区布局

| 区 | 内容 |
|---|---|
| 顶部 | Live2D 红莉栖缩略立绘 + 状态条（"通话中"红点 + 时长 00:42） |
| 中部 | 字幕条（红莉栖当前句淡入）+ 波形动画 + 屏幕共享缩略图（当前帧） |
| 底部 | 三按钮：🎤 麦克风静音 / ✕ 挂断（红大圆）/ 🖥 屏幕共享开关。替代平时 Dock |

### 3.3 状态机（半双工，五态）

```
connecting → listening → processing → speaking → listening（循环）→ ended
```

| 状态 | 触发 | Live2D | 波形 | 字幕 |
|---|---|---|---|---|
| connecting | 点电话按钮 | 待机 | 接通动画 | "正在接通…" |
| listening | 进入通话/她说完 | 倾听表情 | 麦克风波形（青） | "聆听中，请说话" |
| processing | VAD 判你说完→转写中→LLM 中 | 思考表情 | 思考小点动画 | 转写中显"识别中…"，LLM 中显"思考中…" |
| speaking | TTS 播放 | 说话表情+口型 | TTS 波形（橙） | 当前句淡入 |
| ended | 点挂断 | 待机 | 停 | "通话结束" |

**半双工关键**：`speaking` 与 `processing` 状态下**不听麦**（移植原项目 `speakingRef` 逻辑），回避回声误判。无"打断态"。

---

## 4. 实时音频管线细节

### 4.1 VAD（RMS 振幅阈值）

移植原项目 [VoiceCall.tsx:23-27](../../../../amadeus/src/components/VoiceCall.tsx) 参数：

```python
START_THRESH = 0.018   # 开始说话的 RMS 阈值
END_THRESH = 0.012     # 结束说话的 RMS 阈值（低于开始，防抖）
START_FRAMES = 3       # 连续多少帧超阈值才判定"开始说话"
SILENCE_MS = 1100      # 静音持续多久判定"一句话结束"
MAX_UTTERANCE_MS = 15000  # 单次最长录音（防一直不结束）
```

实现：`sounddevice.InputStream` 采集 → 每帧 `numpy` 算 RMS → 阈值状态机。`listening` 态运行 VAD，`processing/speaking` 态跳过。

### 4.2 STT（回合制）

- VAD 判"一句话结束" → 停止录制 → 整段音频
- 复用 [asr_client.py](../../../core/asr_client.py)：`encode_wav`（已有）+ `transcribe`（已有，小米 mimo ASR）
- 录制格式：sounddevice 采集 PCM → encode_wav 转 16-bit 单声道 wav → base64 → transcribe

### 4.3 LLM（DeepSeek 流式）

- 复用 [agent_client.py](../../../core/agent_client.py) / [llm_client.py](../../../core/llm_client.py) 流式接口
- user 消息 = 转写文本 + 屏幕描述（若有）
- 注入人设（KURISU_PERSONALITY）+ 历史

### 4.4 TTS（GPT-SoVITS 流式）

- 沿用 UI redesign spec 规划的 `StreamingTTS` 类（[2026-08-13-ui-redesign-design.md §7](2026-08-13-ui-redesign-design.md)）
- LLM 流式输出 → 分句切分 → GPT-SoVITS 逐句合成 → 播放 + 振幅驱动口型 + 字幕淡入
- API：`POST http://127.0.0.1:9880/tts`，`ref_audio_path=resources/voice_sample.mp3`，`text_lang=ja`
- 降级：GPT-SoVITS 不可用 → 退现有 SAPI（[tts_client.py](../../../core/tts_client.py)）

### 4.5 半双工控制

- `speaking`/`processing` 态 VAD 暂停（移植原项目 `speakingRef`）
- TTS 播放结束 → 回 `listening` 态 → VAD 恢复
- 挂断 → 停 TTS + 停管线 + 恢复 Dock

---

## 5. 屏幕共享与视觉模型

### 5.1 截帧

- `mss` 库定时截屏（每 2-3s），缓存最新一帧（PIL Image）
- 仅缓存最新帧，不存历史（省内存）

### 5.2 视觉理解（开口时附帧）

- 触发时机：VAD 判"一句话结束"→ 转写前，取最新缓存帧
- 调 GPT-4o 视觉：帧 → "当前屏幕描述"（简短，如"用户在 VS Code 编辑 Python 文件"）
- 拼进 DeepSeek user 消息：`{转写文本}\n[当前屏幕: {描述}]`
- 视觉调用频率 = 用户说话频率（省钱），非截帧频率

### 5.3 屏幕共享开关

- 通话 UI 底部 🖥 按钮切换共享开关
- 关闭时：不截帧、不附帧，纯语音通话
- 默认开

---

## 6. 与现有桌宠集成

### 6.1 触发

- Dock 新增 📞 电话按钮（SVG 矢量，与现有 Dock 图标风格一致）
- 点击 → 进入通话态：窗口布局切换 + `VoiceCallController` 启动音频管线
- 挂断 → 退出通话态：停管线 + 恢复 Dock + 窗口平时布局

### 6.2 新增组件

- `core/voice_call.py`：`VoiceCallController` 类，封装 VAD + STT + LLM + TTS 管线 + 状态机
- `ui/widgets/call_view.py`：通话态视图（三区布局 + 波形 canvas + 按钮）
- 复用：`asr_client.py`、`llm_client.py`/`agent_client.py`、`tts_client.py`（降级）/ StreamingTTS（规划）

### 6.3 信号通信

- `VoiceCallController` 用 Qt Signal 驱动 `call_view` 状态切换（phase_changed、subtitle、waveform、elapsed）
- 替代原项目 Web 的 React state + CustomEvent

---

## 7. 错误处理与降级

| 组件失败 | 降级 |
|---|---|
| 麦克风不可用 | 提示 + 退回文字输入模式 |
| VAD 异常 | 提示 + 退"按住说话"按钮式 |
| STT 失败 | 提示"没听清" + 回 listening |
| GPT-SoVITS 不可用 | 退现有 SAPI（tts_client.py） |
| 视觉模型不可用/未配 key | 关屏幕共享，纯语音通话 |
| DeepSeek 失败 | 角色化错误态"…信号不太好，等一下" + 回 listening |

---

## 8. 测试策略

- **VAD 状态机**：mock 音频帧（超阈值/低于阈值序列），断言 startUtterance/endUtterance 触发
- **回合制 STT**：mock 录音 blob + transcribe，断言转写文本传入 LLM
- **状态机五态**：mock 信号，断言 connecting→listening→processing→speaking→listening 切换
- **半双工**：断言 speaking/processing 态 VAD 不触发
- **屏幕附帧**：mock 截帧 + GPT-4o，断言视觉调用频率=说话频率（非截帧频率）
- **降级**：mock 各组件失败，断言降级提示与回退行为
- **UI 切换**：mock 电话按钮点击，断言通话态布局切换 + 管线启停

---

## 9. 验收标准

- [ ] Dock 📞 按钮点击进入通话态，窗口布局切换为三区
- [ ] 通话态五状态机正确切换（connecting/listening/processing/speaking/ended）
- [ ] VAD（RMS 阈值）正确检测说话起止，参数移植原项目
- [ ] 半双工：speaking/processing 态不听麦，无回声误判
- [ ] 回合制 STT：整段录→小米 mimo 转写，复用 asr_client
- [ ] GPT-SoVITS 流式 TTS：红莉栖音色，分句播放，振幅驱动口型，字幕淡入
- [ ] 屏幕共享：mss 截帧缓存，开口时附帧给 GPT-4o，描述注入对话
- [ ] 屏幕共享开关（🖥 按钮）可切换
- [ ] 挂断按钮停管线 + 恢复平时态
- [ ] 各组件失败降级正确（麦克风/STT/TTS/视觉/LLM）
- [ ] 通话 UI 配色与现有 A2 青蓝一致，SVG 矢量按钮

---

## 10. 参考资料

- 原项目 [VoiceCall.tsx](../../../../amadeus/src/components/VoiceCall.tsx)：半双工通话参考（VAD/状态机/波形）
- 原项目 [asr.ts](../../../../amadeus/src/lib/asr.ts)：小米 mimo ASR 配置
- 原项目 [tts.ts](../../../../amadeus/src/lib/tts.ts)：TTS 播放/振幅/口型（CosyVoice，本设计改用 GPT-SoVITS）
- [2026-08-13-ui-redesign-design.md §7](2026-08-13-ui-redesign-design.md)：StreamingTTS 类设计（GPT-SoVITS 流式）
- [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)：TTS 引擎
- user_profile：累积 UI 偏好（透明悬浮/macOS Dock/A2 青蓝）
- project_memory：硬约束（Ctrl+Alt+S 热键、固定按钮、输入面板默认隐藏）
