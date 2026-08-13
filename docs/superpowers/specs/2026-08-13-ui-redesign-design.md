# Amadeus UI 重做设计 spec

> **日期**：2026-08-13
> **状态**：待用户审查
> **上游 spec**：[2026-08-13-amadeus-productization-design.md](./2026-08-13-amadeus-productization-design.md)
> **范围**：UI 视觉/布局/交互重做 + TTS 引擎升级 + 分段气泡 bug 修复

---

## 1. 背景与目标

### 1.1 问题诊断

当前 UI（[desktop_pet.py](../../../desktop_pet.py)）存在四类"玩具感"来源：

1. **整体布局乱**：400×680 固定框内堆叠竖排工具栏（6 按钮 44×220）+ 手机框历史面板（300×400 贴图）+ 输入栏 + 顶部气泡，主次不清。
2. **视觉风格不统一**：iOS 蓝/红/灰 配色 + "邮件手机框"贴图 + Lucida Console 等宽体，风格冲突。
3. **Live2D 融入差**：`paintEvent` 用 `+15` 偏移给工具栏让位，导致人物偏右；全身显示被裁切。
4. **交互细节粗**：emoji 图标廉价感；气泡"先全显再分段"bug（[desktop_pet.py:708-712](../../../desktop_pet.py#L708-L712) `_agent_delta` 流式期间直接打字，`_agent_finished` 后再分段）；无微交互/缓动。

### 1.2 设计目标

- **极简沉浸式**：砍掉手机框、竖排按钮堆，Live2D 居中全身，macOS Dock 风格底部悬浮工具栏。
- **A2 致敬配色**：青蓝 #00d4ff 强调 + 半透青气泡 + 深色玻璃，致敬命运石之门 Amadeus UI，退出操作保留 iOS 红 #ff3b30 辨识。
- **TTS 升级**：GPT-SoVITS V3 少样本推理（红莉栖音色），流式分句 + 可打断 + 口型/字幕绑定（参考 [Lucas1479/Amadeus](https://github.com/Lucas1479/Amadeus) 架构）。
- **修分段气泡 bug**：delta 期间不显示文字，finished 后分段淡入。

### 1.3 不做（YAGNI）

- 不做多角色皮肤切换（单角色红莉栖）
- 不做终端机风格（B 方向，留作彩蛋皮肤，非本期）
- 不做卡片化 SaaS 风（C 方向）
- 不做 GPT-SoVITS 微调训练（少样本推理已够，voice_sample.mp3 15 秒干净人声）
- 不做 ASR 语音输入（TTS 只做输出侧）
- 不做 Wallpaper Engine 桥接（保持独立悬浮窗）

### 1.4 待定/风险

- **GPT-SoVITS 安装体积**：预训练模型 ~1.5GB，首次下载耗时；需写一键安装脚本。
- **GPU 推理延迟**：少样本推理首句可能 1-3 秒，需做"思考中"过渡动画掩盖。
- **Qt SVG 渲染性能**：QSvgRenderer 每帧重绘可能掉帧，备选预渲染 PNG @2x。
- **口型振幅分析**：需实时读音频 PCM 振幅，Python audioop 可做但延迟待测。

---

## 2. 设计决策总览

| 决策项 | 选定方案 | 依据 |
|---|---|---|
| 重做方向 | A 极简沉浸式 | 契合 user_profile 累积偏好（macOS Dock/Apple/透明） |
| 配色 | A2 致敬+iOS 红 | 致敬原作 Amadeus UI，与 Lucas1479 风格一致 |
| 字幕位置 | 顶部（保留 memory） | 用户明确保留 |
| Dock 图标 | SVG 矢量圆润（Phosphor/Tabler 风） | emoji 廉价，矢量无损放大 |
| Dock 按钮数 | 5 个（砍最小化） | 最小化与托盘重复 |
| 输入框 | 与 Dock 互斥显示 | 避免视觉拥挤 |
| 历史抽屉 | 右侧滑入，青灰条 | 替代手机框贴图 |
| TTS 引擎 | GPT-SoVITS V3 少样本 | 用户选定，红莉栖真音色 |
| 参考音频 | voice_sample.mp3 (15s 干净人声) | 落在 5-30s 黄金区间 |
| 分段气泡修法 | delta 静默 + finished 分段淡入 | 根治"先全显再分段" |

---

## 3. 整体布局

### 3.1 窗口

- 尺寸：保持 400×680（与 project_memory 约定一致）
- 透明无边框悬浮（`Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool`）
- 深色玻璃背景：`rgba(8,14,22,0.6)` 渐变

### 3.2 区域划分

```
┌─────────────────────────────────┐
│  [字幕条 顶部 76%宽 自适应高]    │  ← 8% 顶部
│                                 │
│         ┌──────────┐            │
│         │          │            │
│         │  Live2D  │            │  ← 18%-80% 中央
│         │  居中全身 │            │
│         │          │            │
│         └──────────┘            │
│                                 │
│      [输入框 64%宽 6%高]         │  ← 与 Dock 互斥
│   [Dock 60%宽 8%高 居中悬浮]     │  ← 底部 6%
└─────────────────────────────────┘
        ┌──────────┐
        │ 历史抽屉  │  ← 右侧 42%宽 84%高 滑入
        │ (默认隐藏) │
        └──────────┘
```

### 3.3 Live2D 渲染修正

当前 [desktop_pet.py:774-778](../../../desktop_pet.py#L774-L778) `paintEvent`：
```python
target = QRect(
    (self.width() - scaled.width()) // 2 + 15,  # ← +15 偏移给工具栏让位
    ...
)
```
改为：
```python
target = QRect(
    (self.width() - scaled.width()) // 2,  # 正中
    ...
)
```
工具栏移走后无需让位。

---

## 4. 配色系统（A2）

### 4.1 色板

| 角色 | 色值 | 用途 |
|---|---|---|
| glass-dark | `rgba(8,14,22,0.6)` | 窗口玻璃底 |
| cyan | `#00d4ff` | 主强调（Dock hover/字幕边/发送键） |
| cyan-soft | `rgba(0,212,255,0.16)` | 气泡/Dock 背景填充 |
| cyan-border | `rgba(0,212,255,0.4)` | 气泡/Dock 边框 |
| cyan-text | `#7be8ff` | 字幕文字（深底） |
| ios-red | `#ff3b30` | 退出按钮（唯一红） |
| text-dark | `#1d1d1f` | 浅底文字 |
| text-inv | `#f5f5f7` | 深底文字 |

### 4.2 应用

- **字幕条**：`cyan-soft` 背景 + `cyan-border` 边框 + `cyan-text` 文字
- **Dock**：`cyan-soft` 背景 + `cyan-border` 边框，hover 按钮 `cyan` 填充
- **输入框**：`rgba(0,212,255,0.06)` 背景 + `cyan-border` 边框，发送键 `cyan` 实心
- **历史抽屉**：Kurisu 消息 `cyan-soft` + 左 `cyan` 边条；You 消息灰 + 右灰边条
- **退出按钮**：`rgba(255,59,48,0.12)` 背景 + `rgba(255,59,48,0.4)` 边框 + `ios-red` 图标

---

## 5. 组件规格

### 5.1 字幕条（顶部，分段淡入）

- 位置：顶部 8%，水平居中
- 尺寸：宽 76% 自适应（min 80px max 340px），高 36~120px 自适应
- 样式：`cyan-soft` 背景，`cyan-border` 1px 边框，8px 圆角，`cyan-text` 14px Segoe UI
- 行为：TTS 播放某句时该句淡入，播放完淡出，下一句接上（见 §7 TTS 架构）
- bug 修复：delta 期间不调用 `_set_bubble_text`，只显示"思考中"小点动画

### 5.2 Dock 工具栏（底部悬浮，5 按钮）

- 位置：底部 6%，水平居中
- 尺寸：宽 60%，高 8%（约 32px 按钮 + padding）
- 样式：`cyan-soft` 背景，`cyan-border` 边框，`radius-full` 胶囊形
- 按钮：5 个，横排，间距 8px

| 按钮 | 图标 | 功能 | 备注 |
|---|---|---|---|
| 对话 | 气泡轮廓 | 展开输入框（Dock 隐藏） | 默认态 |
| 固定 | 图钉轮廓 | 锁定位置 | 选中态 `cyan` 实心 |
| 设置 | 齿轮轮廓 | 打开设置对话框 | |
| 记录 | 列表轮廓 | 滑出历史抽屉 | |
| 退出 | X 轮廓 | 退出应用 | 唯一 `ios-red` |

### 5.3 Dock 图标（SVG 矢量圆润）

- 风格：Phosphor Icons `regular` 或 Tabler Icons 圆角款（**非 Lucide 硬朗款**，用户要求圆润）
- 规格：
  - 尺寸：默认 32×32，hover 44×44，邻近 38×38
  - 线条：1.6px stroke，`stroke-linecap=round`，`stroke-linejoin=round`
  - 颜色：`cyan` 主，退出 `ios-red`
  - 背景：`rgba(0,212,255,0.08)` 默认，hover `cyan-soft`
- 实现：
  - 5 个 `.svg` 文件存 `resources/icons/`
  - Qt 用 `QSvgRenderer` 加载（无新依赖）
  - 备选：预渲染 PNG @2x（若 SVG 掉帧）
- 来源：[Phosphor Icons](https://phosphoricons.com/)（MIT）或 [Tabler Icons](https://tabler.io/icons)（MIT）

### 5.4 输入框（与 Dock 互斥）

- 位置：底部 3%，水平居中
- 尺寸：宽 64%，高 6%（约 48px）
- 样式：`rgba(0,212,255,0.06)` 背景，`cyan-border` 边框，`radius-full` 胶囊
- 交互：
  - 点💬 → Dock 淡出 + 输入框淡入（200ms）
  - 发送/Esc → 输入框淡出 + Dock 淡入
- 发送键：`cyan` 实心圆，白箭头，hover 加深

### 5.5 历史抽屉（右侧滑入）

- 位置：右侧 42% 宽，84% 高
- 样式：`rgba(8,14,22,0.85)` 半透深底 + `cyan-border` 左边框
- 内容：
  - Kurisu 消息：`cyan-soft` 背景 + 左 2px `cyan` 边条 + `cyan-text`
  - You 消息：`rgba(255,255,255,0.06)` 背景 + 右 2px 灰边条 + 浅灰文字
- 字体：13px Segoe UI（替代当前 Lucida Console 等宽体）
- 滚动：青色细滚动条（6px 宽）
- 动画：300ms `cubic-bezier(.4,0,.2,1)` 滑入

---

## 6. 动效规格

### 6.1 Dock 悬浮放大（macOS 经典）

- 中心按钮：32px → 44px（1.375x）
- 邻近按钮：32px → 38px（1.1875x）
- 曲线：`cubic-bezier(.34,1.56,.64,1)`（带回弹）
- 时长：200ms

### 6.2 字幕分段淡入

- fade-in：180ms `ease-out`，opacity 0→1
- hold：按字数 `min(1500 + char_count*80, 6000)` ms
- fade-out：200ms `ease-out`，opacity 1→0
- 实现：`QGraphicsOpacityEffect` + `QPropertyAnimation`

### 6.3 历史抽屉滑入

- 300ms `cubic-bezier(.4,0,.2,1)`（Material 标准）
- 右侧 42% 宽，从 `right: -42%` 滑到 `right: 0`

### 6.4 输入框/Dock 互斥切换

- 200ms `ease-out` opacity 交叉淡入淡出

---

## 7. TTS 架构（GPT-SoVITS V3 集成）

### 7.1 引擎

- **GPT-SoVITS V3**（[RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)，MIT）
- 模式：少样本推理（zero-shot/few-shot），无需训练
- 参考音频：[resources/voice_sample.mp3](../../../resources/voice_sample.mp3)（15 秒红莉栖干净人声）
- 硬件：用户有 GPU（CUDA 加速）

### 7.2 统一流式架构

```
LLM 流式输出
    ↓
分句切分（按 。！？\n）
    ↓
TTS 逐句合成（GPT-SoVITS API）
    ↓
音频流播放（PyAudio / sounddevice）
    ↓
    ├─→ 振幅分析 → ParamMouthOpenY（口型）
    └─→ 当前句 → 字幕条淡入
```

### 7.3 StreamingTTS 类设计

替换当前 [core/tts_client.py](../../../core/tts_client.py) 的 `SpeechPlayer`：

```python
class StreamingTTS(QObject):
    sentence_start = Signal(str)   # 驱动字幕淡入
    amplitude = Signal(float)      # 驱动口型 ParamMouthOpenY
    speaking_changed = Signal(bool)

    def __init__(self, gpt_sovits_url: str, refer_audio: str):
        ...

    def speak_streaming(self, sentences: list[str]) -> None:
        """逐句合成播放，可打断。"""
        # 每句：POST gpt_sovits_url/tts → 音频流 → 播放 + 振幅回调
        # on_sentence_start.emit(sentence) 播放前触发
        # on_amplitude.emit(level) 每帧触发

    def stop(self) -> None:
        """打断当前播放（实现 stop，当前是 pass）。"""
        # cancel 当前音频流 + 清空队列
```

### 7.4 GPT-SoVITS API 调用

- 启动：`python api_v2.py`（默认端口 9880）
- 请求：`POST http://127.0.0.1:9880/tts`
  - `text`: 要合成的日语句
  - `text_lang`: `ja`
  - `ref_audio_path`: `resources/voice_sample.mp3`
  - `prompt_lang`: `ja`
  - `prompt_text`: 参考音频对应文本（若有，提升准确度）
- 返回：音频流（WAV/MP3）

### 7.5 口型振幅分析

- 播放时用 `audioop.rms()` 实时计算 PCM 振幅
- 归一化到 0.0-1.0
- 通过 `amplitude` 信号驱动 Live2D `ParamMouthOpenY`
- 替代当前 [live2d_page.html:114](../../../live2d/live2d_page.html#L114) 的 `Math.random()` 假口型

### 7.6 可打断

- 用户说话/新消息时调 `stop()`
- cancel 当前 `requests` 请求 + 停止音频流 + 清空待播队列
- 通过 epoch 机制防止旧句音频在中断后重新出现（参考 Lucas1479 TurnCoordinator）

---

## 8. 分段气泡 bug 修复

### 8.1 根因

[desktop_pet.py:708-712](../../../desktop_pet.py#L708-L712)：
```python
def _agent_delta(self, text: str) -> None:
    self._streamed_reply += text
    if not self._history_expanded:
        self.reply_bubble.show()
    self._set_bubble_text(self._latest_line(self._streamed_reply))  # ← 流式期间直接打字
```
delta 期间把累积最新行直接打到气泡，用户看到"打字机式全显"；`_agent_finished` 后再 `_show_layered_bubbles` 从头分段 → 视觉上"先全显再分段"。

### 8.2 修复方案

1. `_agent_delta` 不再调 `_set_bubble_text`，改为只显示"思考中"小点动画（3 个 `cyan` 点呼吸）
2. `_agent_finished` 后调 `_show_layered_bubbles`，每段用 `QGraphicsOpacityEffect` 做 180ms 淡入
3. 取消当前 `QTimer.singleShot` 硬切换，改为 opacity 动画过渡
4. 与 TTS 集成：分段与 TTS 逐句播放对齐（TTS 播某句 → 该句字幕淡入）

---

## 9. 实施顺序建议

1. **bug 修复**（1 步）：分段气泡 delta 静默 + finished 分段淡入
2. **布局重做**（核心）：
   - 砍手机框历史面板 + 竖排工具栏
   - Live2D 居中（修 `+15` 偏移）
   - Dock 底部悬浮（SVG 图标）
   - 输入框/Dock 互斥
   - 历史抽屉右侧滑入
3. **配色切换**（A2 青蓝）：全局替换样式表
4. **动效**：Dock 放大 + 字幕淡入 + 抽屉滑入
5. **TTS 集成**（独立大块）：
   - GPT-SoVITS 安装脚本
   - StreamingTTS 类实现
   - 口型/字幕绑定
   - 可打断

---

## 10. 验收标准

- [ ] 分段气泡不再"先全显再分段"，delta 期间显示"思考中"动画
- [ ] Live2D 人物水平居中，无 `+15` 偏移
- [ ] 砍掉手机框贴图（mail_phone_frame.png 不再使用）
- [ ] 砍掉竖排工具栏，改为底部 Dock 横排 5 按钮
- [ ] Dock 图标为 SVG 矢量圆润风格（非 emoji）
- [ ] 配色为 A2 青蓝（#00d4ff）+ iOS 红退出
- [ ] 字幕条顶部，分段淡入（180ms）
- [ ] 输入框与 Dock 互斥切换（200ms 淡入淡出）
- [ ] 历史抽屉右侧滑入（300ms），青灰条消息样式
- [ ] GPT-SoVITS 集成：voice_sample.mp3 参考音频，红莉栖音色
- [ ] TTS 流式分句，字幕与播放对齐
- [ ] TTS 可打断（stop() 不再是 pass）
- [ ] 口型由音频振幅驱动（非 Math.random）

---

## 11. 参考资料

- [Lucas1479/Amadeus](https://github.com/Lucas1479/Amadeus)：架构启发（流式时间线、口型字幕绑定、可打断）
- [Aqua-TTS](https://github.com/Lucas1479/Aqua-TTS)：GPT-SoVITS V3 实时推理运行时
- [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)：TTS 引擎
- [rany2/edge-tts](https://github.com/rany2/edge-tts)：备选 TTS（若 GPT-SoVITS 不可用时降级）
- [Phosphor Icons](https://phosphoricons.com/)：矢量图标（圆润风格）
- [Qt QSvgRenderer 文档](https://doc.qt.io/qt-6/qsvgrenderer.html)：SVG 加载
- user_profile：累积 UI 偏好（macOS Dock/Apple/透明/iOS 配色）
- project_memory：硬约束（Ctrl+Alt+S 热键、固定按钮、输入面板默认隐藏）
