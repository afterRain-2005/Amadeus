# PRD：Companion 周度行为总结 + 主动来电

> 状态：已定稿，待实施
> 范围：仅 Companion 子系统（含其在 desktop_pet / settings_dialog / config 的接入点）
> 依赖：复用现有「电话模式」`core/voice_call.py` + `ui/call_view.py`

## 1. 背景与目标

### 1.1 现状
- Companion 主动问候子系统已实现（[core/companion](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/core/companion)），修复启动未建表 bug 后能正常出吐槽气泡。
- 已有「电话模式」（[core/voice_call.py](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/core/voice_call.py) + [ui/call_view.py](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/ui/call_view.py)），但只能由用户点击 Dock 电话按钮进入，Amadeus 不会主动来电。
- 用户希望：Amadeus 定期（约一周一次）总结「我」最近的行为，并**主动发起双向语音通话**——先开口播报总结，再继续对话。

### 1.2 目标
- 新增「周度行为总结 + 主动来电」能力：到点后 Amadeus 自动总结最近一周行为，主动进入电话模式并先播报总结。
- 设置页可开关（可选开启/关闭），默认**关闭**（主动来电打扰性强，默认不打扰）。
- 复用现有电话模式（VoiceCallController + CallView + 半双工 VAD/STT/LLM/TTS 管线）。

### 1.3 非目标
- 不改 chat / agent / TTS / claw / research 等 Companion 以外的子系统逻辑（电话模式仅做「主动开口」的最小扩展，见 §4.2）。
- 不做多角色、不做 IM 通知、不做「来电铃声/接听/拒绝」交互（Amadeus 直接开口，用户可挂断）。
- 不做情绪识别、不做地理位置、不做摄像头。
- 不把「周总结」做成长期记忆（facts/episodes/向量），那是 P1 记忆层的事。

### 1.4 参考实现
- 现有 Companion 主动问候设计：[docs/superpowers/specs/2026-08-16-companion-proactive-greeting-design.md](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/docs/superpowers/specs/2026-08-16-companion-proactive-greeting-design.md)
- 现有电话模式设计：[docs/superpowers/specs/2026-08-13-phone-call-mode-design.md](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/docs/superpowers/specs/2026-08-13-phone-call-mode-design.md)

## 2. 已确认决策（用户拍板）

| 维度 | 决策 |
|---|---|
| 触达形式 | 双向语音通话（复用电话模式），Amadeus 先开口总结再进入对话 |
| 周期 | 约一周一次（默认 7 天，可配置 1–30 天） |
| 数据来源 | Companion 传感器 + 最近聊天记录 + lightweight_memory 记忆库 |
| 开关 | 设置页可选开启/关闭，**默认关闭** |
| 触碰边界 | **允许**最小扩展 `core/voice_call.py` 实现 speak-first（`start(initial_text=...)`） |
| 首次触发 | **安装即可按周期触发**（`last_digest_ts=None` 视为已到期，无首次宽限） |

## 3. 架构设计

### 3.1 数据流

```
WeeklyDigestTrigger（core/companion/digest.py 新增，随 _companion_tick 30s 周期检查）
   ├ 是否开启 enabled？
   ├ 是否到期（距 last_digest_ts >= interval_days）？
   ├ 是否在静音时段 / 用户通话中 / 用户忙碌？
   ├ 聚合上下文：
   │    ├ sensors 快照（build_snapshot，已有）
   │    ├ 最近聊天记录（sessions.json 最近 N 条，最多 14 条）
   │    └ lightweight_memory 最近 7 天记忆（source IN companion/digest）
   ├ 调 LLM 生成周总结（红莉栖语气，=== 双语格式 + [emotion:xxx]）
   └ 触发主动来电：
        desktop_pet 进入电话模式 + VoiceCallController.speak_first(总结文本)
             ↓
        Amadeus 播报总结 → 回 listening → 用户回应 → 半双工循环（复用现有管线）
```

### 3.2 触发条件（优先级从高到低）

1. `weekly_digest.enabled == true`
2. `last_digest_ts is None`（首次）或 `now - last_digest_ts >= interval_days`（默认 7 天）→ 视为到期（安装即可按周期触发，无首次宽限）
3. 非静音时段（复用 companion `quiet_hours`）
4. 用户当前不在通话态（`pet._in_call == false`）
5. 用户最近 5 分钟内没有主动消息（复用 `on_user_message` 冷却，避免打断对话）
6. 触发后写 `last_digest_ts`，本次周期内不再重复触发

### 3.3 总结生成

- Prompt 新增 `KURISU_WEEKLY_DIGEST_INSTRUCTION`（[core/companion/prompts.py](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/core/companion/prompts.py)）：
  - 你是牧濑红莉栖，用傲娇/毒舌但关心的语气总结用户最近一周的行为。
  - 输入包含：行为快照（前台窗口/空闲/工作时长）、最近聊天摘要、记忆库条目。
  - 输出格式沿用 `KURISU_OUTPUT_FORMAT`（中文 `===` 日语 + `[emotion:xxx]`），保证电话模式 TTS 能正确取日语段。
  - 长度适中（气泡/字幕可读，中文 ≤ 200 字）。
- 调用路径：复用 `route_and_send`（`system_role="companion"`, `skip_history=True`, `inject_system_prompt`），避免新开 LLM 通路；或新增 `digest.py` 内直接调 `Evaluator._call_llm` 风格的最小调用。**实现期二选一，倾向复用 route_and_send**（与现有 companion 表达层一致）。

### 3.4 主动来电（speak-first）

- 现状：`VoiceCallController.start()` 固定走 `connecting → (1.3s) → listening`，无「先开口」路径。
- 目标：新增 `start(initial_text: str | None = None)` 或等价 `speak_first(text)`，进入 connecting 后先 `speaking` 播报 `initial_text`，播完由 `speaking_changed(False) → _on_tts_speaking_changed` 回 `listening`。
- 播报复用现有流式 TTS 接口：`speak_streaming_start / speak_streaming_append / speak_streaming_end`（[core/voice_call.py](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/core/voice_call.py#L364-L396) 已具备），或用 `speak_with_options` 整体播报。

## 4. 模块与文件改动

### 4.1 Companion 内（必改，属于任务边界内）
- 新增 [core/companion/digest.py](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/core/companion/digest.py)：`WeeklyDigestTrigger`（到期判断 + 上下文聚合 + 总结生成 + 触发回调）。
- [core/companion/storage.py](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/core/companion/storage.py)：新增 `record_digest(text, topic, emotion)`、`last_digest_ts()`、`recent_memories(days=7)`。
- [core/companion/prompts.py](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/core/companion/prompts.py)：新增周总结 prompt。
- [config.py](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/config.py)：`COMPANION_DEFAULTS` 新增 `weekly_digest` 子配置。
- [desktop_pet.py](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/desktop_pet.py) companion 接入块：实例化 `WeeklyDigestTrigger`，在 `_companion_tick` 里调 `digest_trigger.check(...)`，命中则调「进入主动来电」回调。
- [ui/settings_dialog.py](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/ui/settings_dialog.py) Companion tab：新增「周度总结来电」区域（开关 + 周期 + 数据源 + 上次来电时间 + 测试按钮）。

### 4.2 Companion 以外（已确认纳入本次范围）
- [core/voice_call.py](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/core/voice_call.py)：`VoiceCallController` 新增 speak-first 能力（`start(initial_text: str | None = None)`），默认 None 走原逻辑，向后兼容。

## 5. 配置与数据

### 5.1 config.py 新增

```python
COMPANION_DEFAULTS["weekly_digest"] = {
    "enabled": False,          # 主动来电默认关闭（打扰性强）
    "interval_days": 7,        # 约一周一次
    "include_chat": True,      # 是否纳入最近聊天记录
    "include_memory": True,    # 是否纳入 lightweight_memory
    "include_sensors": True,   # 是否纳入传感器行为快照
}
```

### 5.2 storage 新增接口（复用 lightweight_memory，source='digest'）

```python
def record_digest(text: str, topic: str, emotion: str) -> int: ...
def last_digest_ts() -> str | None: ...        # 最近一次周总结时间
def recent_memories(days: int = 7) -> list[str]: ...  # 最近 N 天记忆条目文本
```

## 6. 设置页（Companion tab 内）

- 新增 section「WEEKLY DIGEST CALL」：
  - `启用周度总结来电`（QCheckBox，默认关）
  - `总结周期（天）`（QLineEdit，默认 7）
  - `数据来源`：三个 checkbox（传感器 / 聊天记录 / 记忆库）
  - `上次来电时间`（只读 QLabel）
  - `立即测试来电`（QPushButton，手动触发一次）

## 7. 测试方案

### 7.1 单元测试
- `tests/companion/test_digest.py`：
  - 到期判断（距上次 >= 7 天 / 未到期 / 首次无记录）
  - 静音时段 / 用户通话中 / 用户刚发消息 → 不触发
  - 上下文聚合（sensors + chat + memory 合并，空数据降级）
  - LLM 总结失败 → 降级走模板总结（红莉栖固定话术）
- `tests/companion/test_storage.py`：`record_digest` / `last_digest_ts` / `recent_memories` CRUD。
- `tests/test_voice_call.py`（若纳入 voice_call 改动）：`start(initial_text)` 先 speaking 播报、播完回 listening。

### 7.2 手动验收
1. 设置页开启「周度总结来电」，周期临时改为 0 天 → 触发主动来电 → Amadeus 先开口总结 → 能对话、能挂断。
2. 关闭开关 → 不再触发。
3. 静音时段 / 通话中 → 不触发。
4. 关掉网络 → 总结降级模板，仍能进入通话态或优雅跳过。

## 8. 风险与限制

| 风险 | 缓解措施 |
|---|---|
| 主动来电打扰用户 | 默认关闭 + 静音时段 + 用户通话/刚发消息时冷却 |
| 周总结生成质量不稳定 | prompt 约束 + 长度上限 + 失败降级模板 |
| 数据来源含聊天记录可能泄露隐私 | 仅本地使用、不出本机；数据源 checkbox 可关 |
| 首次运行无记录即视为到期 | 默认关闭 + 用户显式开启后才可能触发；无宽限为用户确认的行为 |
| voice_call 改动影响现有通话 | 只新增 `initial_text` 可选参数，默认 None 走原逻辑，向后兼容 |

## 9. 已确认事项

1. 允许本次触碰 `core/voice_call.py`（最小新增 `start(initial_text=...)`，向后兼容）。
2. 默认开关：**关闭**。
3. 周期范围：**1–30 天**，默认 7。
4. 首次触发：**安装即可按周期触发**（`last_digest_ts=None` 视为已到期，无宽限）。

## 10. 实施步骤

1. `config.py` 加 `weekly_digest` 默认配置。
2. `core/companion/storage.py` 加 digest 相关接口 + 测试。
3. `core/companion/prompts.py` 加周总结 prompt。
4. 新增 `core/companion/digest.py`（触发 + 聚合 + 总结）+ 测试。
5. （若允许）`core/voice_call.py` 加 `start(initial_text=...)` + 测试。
6. `desktop_pet.py` companion 接入块接入 `WeeklyDigestTrigger` + 主动来电回调。
7. `ui/settings_dialog.py` Companion tab 加「周度总结来电」区域。
8. 全量回归（`pytest tests/`）+ 手动验收。

## 11. 用户验证清单

- [ ] 设置页开启开关，周期调小触发主动来电，Amadeus 先开口总结
- [ ] 能正常对话、正常挂断，之后回到普通桌宠态
- [ ] 关闭开关后不再触发
- [ ] 静音时段 / 通话中 / 刚发消息时不触发
- [ ] 断网时总结降级不崩溃
- [ ] 普通 chat / 主动吐槽气泡 / 电话模式均不被破坏
