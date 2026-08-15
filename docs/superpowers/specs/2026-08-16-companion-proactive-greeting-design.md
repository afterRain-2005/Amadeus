# Companion 主动问候（伪春菜式陪伴）设计

> 日期：2026-08-16
> 状态：设计已与用户分段确认
> 对应 Spec：[2026-08-13-amadeus-productization-design.md](./2026-08-13-amadeus-productization-design.md) §3 三层架构、§6 感知层、P3 主动阶段
> 范围：实现红莉栖主动观察用户活动并吐槽/关心的 companion 子系统

## 1. 背景与目标

需求文档（六系统）中 companion 系统要求：多级主动问候、检测用户活动（浏览网页/工作节奏）→ 主动吐槽、参考 Bangumi 伪春菜、本地记忆系统、聊天聚焦。

本 spec 范围 = companion 主动问候子系统。**不做**完整 P1 记忆层（facts/episodes/向量），不做 claw/research。

## 2. 已确认决策（用户分段批准）

| 维度 | 决策 | 依据 |
|---|---|---|
| 感知范围 | 全感知：空闲 + 前台窗口 + 节奏统计 + 剪贴板 + 屏幕 | 用户选项 D；产品化设计 §6 |
| 剪贴板/屏幕默认 | 关 | 产品化设计 §6 隐私边界 |
| 触发策略 | 混合：硬阈值必说场景走模板 + LLM 决策可选场景生成内容 | 平衡成本与拟人度 |
| 集成方式 | 复用 chat 路径（虚拟用户输入送 route_and_send，system_role="companion", skip_history=True） | 用户选最简方案，代码改动最小 |
| 记忆 | 轻量 SQLite `lightweight_memory` 表，与未来 P1 同库同 schema | 重启保留 + 平滑过渡 P1 |
| 用户控制 | 全设置页：总开关 + 传感器逐项开关 + 静音时段 + 频率滑块 + 每日上限 | 用户选商业级标配 |
| 调度器架构 | 方案 B：事件驱动 + 节流（传感器各自 QTimer 轮询，信号变化触发评估器，5min LLM 节流窗口） | 平衡响应速度与 LLM 成本 |

## 3. 架构总览

```
┌──────────────── desktop_pet.py（PySide6 overlay 闭包内）────────────────┐
│  CompanionController                                                    │
│    ├ Sensors（core/companion/sensors.py）                              │
│    │   ├ ActiveWindowSensor    QTimer 2s   GetForegroundWindow        │
│    │   ├ ActivityTracker       QTimer 30s  GetLastInputInfo             │
│    │   ├ IdleStateTracker      派生         基于 ActivityTracker       │
│    │   ├ ClipboardSensor       QTimer 1s   win32clipboard diff（默认关）│
│    │   └ ScreenSensor          按需触发    mss 截屏→视觉 LLM（默认关）  │
│    ├ Evaluator（core/companion/evaluator.py）                          │
│    │   ├ L1 硬阈值规则引擎（必说场景，零 LLM 成本）                    │
│    │   └ L2 LLM 决策器（可选场景，5min 节流 + 10min 全局冷却）         │
│    ├ Scheduler（core/companion/scheduler.py）                          │
│    │   ├ 静音时段 / 频率概率门控 / 每日上限 / 用户对话后冷却           │
│    └ 输出：route_and_send(text, system_role="companion", skip_history=True)│
└────────────────────────────────────────────────────────────────────────┘
                ↓ 复用现有表达层
        emotion_parser → _send_emotion + _show_layered_bubbles + TTS
```

**架构原则**：
- CompanionController 是 desktop_pet.py 闭包内类（参考 AgentSignals 模式），不另起进程
- 所有失败静默降级，companion 永不影响主对话流程
- 与 P1 记忆层同库 `data/memory.db`，schema 兼容未来扩展

## 4. 感知层传感器规格

| 传感器 | 周期 | win32 API | 数据字段 | 默认 | 隐私 |
|---|---|---|---|---|---|
| ActiveWindowSensor | 2s | `GetForegroundWindow` + `GetWindowTextW` | `{window_title, process_name, since_ts}` | 开 | 低 |
| ActivityTracker | 30s | `GetLastInputInfo` | `{last_input_ts, idle_seconds, work_session_minutes}` | 开 | 低 |
| IdleStateTracker | 派生 | （基于 ActivityTracker） | `{idle_state: active/idle/away, since_ts}` | 开 | 低 |
| ClipboardSensor | 1s 节流 | `win32clipboard` + diff | `{hash, length, preview_50chars}` | **关** | 中 |
| ScreenSensor | 按需 | `mss` 截屏 → 视觉 LLM | `{frame_jpg_b64, ocr_text}` | **关** | 高 |

### ContextSnapshot（喂给 LLM 决策器的紧凑格式）

```python
@dataclass
class ContextSnapshot:
    timestamp: str  # ISO8601
    local_time: str  # "14:30 周二"
    is_deep_night: bool  # 23:00-06:00
    idle_seconds: int
    work_session_minutes: int  # 连续工作分钟（无 >5min 中断）
    idle_state: str  # active/idle/away
    active_window_title: str
    active_process: str
    window_changed_recently: bool  # 30s 内切换过窗口
    last_companion_greeting_ts: str | None
    last_companion_topic: str | None
    greeting_count_today: int
    clipboard_preview: str | None  # None 或前 50 字符（含敏感词过滤）
    screen_ocr_text: str | None
```

### 隐私边界（产品化设计 §6）
- 每个传感器在设置页独立开关
- Clipboard / Screen 默认关
- 设置页"当前感知到的上下文"实时预览
- 所有感知数据本地存储，不出本机
- **不记录按键内容**（只看空闲时长，不看具体按键）
- 剪贴板含 `password`/`key`/`token` 等关键词时不发送给 LLM

## 5. 评估器与调度器

### L1 硬阈值规则（必说场景，预设模板）

| 触发条件 | 模板（红莉栖语气） | emotion |
|---|---|---|
| `idle_seconds > 900` (15min) | "盯着屏幕发呆也修不好 bug，不如起来走走？" | `idle` |
| `is_deep_night and work_session_minutes > 30` | "现在 {time} 了，你不睡觉我也不睡啊" | `sleepy` |
| `work_session_minutes > 120` (2h) | "你已经坐了 {n} 分钟了，颈椎不要了？" | `concern` |
| `window_changed_recently and greeting_count_today == 0` | "切换窗口切得这么勤，是在摸鱼吧？" | `tease` |

模板支持变量插值，从 `KURISU_GREETINGS` 扩展为 `KURISU_PROACTIVE_TEMPLATES`（放 `core/companion/prompts.py`）。

### L2 LLM 决策器（可选场景）

```python
def _llm_decide(self, snapshot: ContextSnapshot) -> GreetingDecision | None:
    prompt = self._build_prompt(snapshot)  # 注入 SOUL.md + 上下文快照
    resp = call_llm(
        model=config.chat_model,
        messages=[
            {"role": "system", "content": KURISU_PROACTIVE_INSTRUCTION},
            {"role": "user", "content": json.dumps(snapshot.dict())}
        ],
        max_tokens=100,
        response_format={"type": "json_object"},
        temperature=0.8
    )
    data = json.loads(resp)
    if not data.get("should_speak"):
        return None
    return GreetingDecision(
        text=data["text"],
        emotion=data.get("emotion", "neutral"),
        topic=data.get("topic", "general"),
        source="llm"
    )
```

**KURISU_PROACTIVE_INSTRUCTION 要点**（`core/companion/prompts.py`）：
- 你是牧濑红莉栖，主动观察用户在做什么并吐槽/关心
- 风格：傲娇、毒舌但关心、偶尔卖萌，参考石头门原作
- 长度限制：≤30 字（气泡 ≤140px 宽）
- 永远不暴露你是 AI 助手
- JSON 输出：`{"should_speak": bool, "text": str, "emotion": str, "topic": str}`
- `should_speak=false` 当用户明显在专注工作/会议/重要操作时

### 节流策略

| 维度 | 窗口 | 实现 |
|---|---|---|
| 同类信号 LLM 调用 | 5min | 内存集合记 `(signal_type, last_llm_ts)` |
| 问候间隔（全局） | 10min | `lightweight_memory.last_greeting_ts` |
| 静音时段 | 23:00-08:00（可改） | scheduler.check_quiet_hours() |
| 频率滑块 | low=20% / mid=50% / high=100% | `random.random() < frequency_ratio` 概率门控 |
| 每日上限 | 30 次 | `lightweight_memory.greeting_count_today` |
| 用户对话后冷却 | 5min | `_last_user_msg_ts`，避免打断对话节奏 |

### 调度器执行顺序

```
传感器信号变化 → Evaluator.evaluate(snapshot)
   ├─ L1 硬阈值命中？─Yes→ 取模板
   │                  No↓
   ├─ 总开关？─No→ return None
   ├─ 静音时段？─Yes→ return None（idle_state=away 超 1h 例外）
   ├─ 频率概率门控？─No→ return None
   ├─ LLM 节流允许？─No→ return None
   ├─ 全局冷却允许？─No→ return None
   ├─ 每日上限？─Yes→ return None
   └─ LLM 决策 → GreetingDecision or None
                              │
                              └→ CompanionController.speak(decision)
                                   ├─ 写 lightweight_memory
                                   └─ route_and_send(text, system_role="companion", skip_history=True)
```

## 6. 记忆层：lightweight_memory 表

```sql
-- core/companion/storage.py 创建表（data/memory.db，与 P1 同库）
CREATE TABLE IF NOT EXISTS lightweight_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,           -- ISO8601
    source TEXT NOT NULL,       -- 'companion' | 'user_feedback'
    text TEXT NOT NULL,
    topic TEXT,
    emotion TEXT,
    user_feedback TEXT,         -- 'positive'|'negative'|'neutral'|NULL
    feedback_ts TEXT
);
CREATE INDEX idx_lm_ts ON lightweight_memory(ts);
CREATE INDEX idx_lm_source_topic ON lightweight_memory(source, topic);
```

**查询接口**：
- `record_greeting(text, topic, emotion)`
- `record_feedback(greeting_id, feedback)`
- `last_greeting_ts() -> str | None`
- `greeting_count_today() -> int`
- `recent_topics(hours=2) -> set[str]`
- `similar_topic_exists(topic, hours=6) -> bool`

**与 P1 关系**：P1 实现时 `lightweight_memory` 可作为 `episodes` 子集被吸收，schema 兼容（保留 `source` 字段区分来源）。

## 7. 集成方式：route_and_send 扩展

修改 `core/backend_router.py` 的 `route_and_send` 签名（向后兼容）：

```python
def route_and_send(
    *,
    text: str,
    history: list[dict] | None = None,
    on_delta=None,
    on_status=None,
    on_approval=None,
    # 新增参数
    system_role: str = "user",          # 'user' | 'companion' | 'system'
    skip_history: bool = False,         # True 时不写 conversation_history
    inject_system_prompt: str | None = None,  # companion 注入主动问候指令
) -> tuple[str, str]:
    ...
    if not skip_history and history is not None:
        history.append({"role": "user", "content": text})
    
    messages = []
    if inject_system_prompt:
        messages.append({"role": "system", "content": inject_system_prompt})
    # + SOUL.md / KURISU_PERSONALITY / 历史
```

**调用点**（`desktop_pet.py` 闭包内）：

```python
def _companion_speak(decision: GreetingDecision) -> None:
    record_greeting(decision.text, decision.topic, decision.emotion)
    reply, _ = route_and_send(
        text=decision.text,
        history=None,
        on_delta=..., on_status=...,
        system_role="companion",
        skip_history=True,
        inject_system_prompt=KURISU_PROACTIVE_PASS_THROUGH.format(text=decision.text),
    )
```

**KURISU_PROACTIVE_PASS_THROUGH**：
> "你接下来要说的话已经准备好了，把以下内容用你的语气自然说出，可以微调措辞但不要改变意思：\n\n{text}"

这样 LLM 会把模板/已决策文本二次加工成红莉栖语气，既保留决策结果又保证角色感。

## 8. 设置页：companion tab

新增 `ui/settings_dialog.py` 第 6 个 tab "Companion"：

- [✓] 启用主动陪伴
- 感知器逐项开关（5 个传感器 + 当前值实时显示）
- 触发策略：静音时段 / 频率（低/中/高）/ 每日上限
- 当前上下文预览（只读文本框，显示 ContextSnapshot 内容）
- [测试问候] [清空记忆] 按钮

**config.py 新增**：

```python
COMPANION_DEFAULTS = {
    "enabled": True,
    "sensors": {
        "active_window": True,
        "activity": True,
        "idle": True,
        "clipboard": False,
        "screen": False,
    },
    "quiet_hours": {"start": "23:00", "end": "08:00"},
    "frequency": "mid",
    "daily_limit": 30,
}
```

## 9. 错误处理与降级

| 故障 | 行为 |
|---|---|
| 任一传感器 win32 调用失败 | 该传感器单独 stop，其他继续；scheduler 跳过该字段 |
| LLM 决策器超时（>5s）或网络失败 | 降级走 L1 硬阈值模板（即便本场景非必说） |
| LLM 返回非法 JSON | 重试 1 次（temperature=0）；仍失败则降级模板 |
| SQLite 损坏 | 备份后重建空表，scheduler 冷却重置 |
| Clipboard/Screen 开关被关 | 该字段在 ContextSnapshot 中为 None |
| 用户正在对话（5min 内） | scheduler 不触发，避免打断 |
| route_and_send 内部失败 | 复用现有降级链（agent→chat→本地） |
| companion 总开关被关 | CompanionController.stop() 全停 |
| 静音时段但 idle_state=away 超 1h | 破例触发"很久没碰电脑了，还在吗"模板 |

**降级原则**：companion 永不影响主对话流程，所有失败静默降级或走模板。

## 10. 测试策略

### 单元测试
- `tests/companion/test_sensors.py`：5 传感器各自 mock win32/mss，验证 snapshot 字段
- `tests/companion/test_evaluator.py`：L1 硬阈值规则矩阵 + L2 LLM 决策（mock call_llm）+ LLM 失败降级
- `tests/companion/test_scheduler.py`：节流/冷却/静音/概率门控/每日上限
- `tests/companion/test_storage.py`：CRUD + 索引查询 + 同主题去重 + today 计数
- `tests/test_route_and_send_companion.py`：skip_history=True 不写历史 + inject_system_prompt 注入位置 + system_role 标记不影响回复解析

### 集成测试
- `tests/companion/test_integration.py`：模拟 1 小时活动序列（工作→空闲→深夜），验证触发模式；端到端 snapshot → evaluate → speak → route_and_send

### 手动验收清单
1. 启动桌宠，发条消息确认 chat 正常
2. 不动鼠标 16min，确认红莉栖主动说"盯着屏幕发呆..."
3. 切到 B 站窗口，等待 LLM 决策（5min 节流后），确认红莉栖吐槽"在摸鱼吧"
4. 设置页关闭 companion 总开关，确认不再主动说话
5. 设置页关闭前台窗口传感器，确认不再有窗口相关吐槽
6. 调整静音时段为当前时间，确认不触发
7. 频率调"低"，观察触发概率明显下降
8. 清空记忆按钮，确认 lightweight_memory 表被清空
9. 关掉网络，触发 LLM 决策场景，确认走模板降级
10. 验证 chat history 不被 companion 污染

## 11. 文件结构

**新建**：
- `core/companion/__init__.py`
- `core/companion/sensors.py` — 5 传感器 + ContextSnapshot
- `core/companion/evaluator.py` — Evaluator + GreetingDecision + 硬阈值规则
- `core/companion/scheduler.py` — Scheduler（节流/静音/概率/上限）
- `core/companion/storage.py` — lightweight_memory 表 CRUD
- `core/companion/prompts.py` — KURISU_PROACTIVE_INSTRUCTION + TEMPLATES + PASS_THROUGH
- `tests/companion/__init__.py`
- `tests/companion/test_sensors.py`
- `tests/companion/test_evaluator.py`
- `tests/companion/test_scheduler.py`
- `tests/companion/test_storage.py`
- `tests/companion/test_integration.py`
- `tests/test_route_and_send_companion.py`

**修改**：
- `core/backend_router.py` — route_and_send 扩展（向后兼容）
- `desktop_pet.py` — CompanionController 闭包内类 + _companion_speak 接入
- `ui/settings_dialog.py` — companion tab
- `config.py` — COMPANION_DEFAULTS
- `requirements.txt` — pywin32（若无）、mss（已有）

## 12. 不做（YAGNI）

- ❌ 长期记忆（"她记得你上次说过什么"）—— 属 P1 范围
- ❌ 情绪感知（用户摄像头表情识别）
- ❌ 地理位置感知
- ❌ IM 通知集成 —— 属 claw 范围
- ❌ 多角色 companion —— 只有红莉栖
- ❌ companion 主动调 agent 工具 —— 只说，不做

## 13. 待定 / 风险

- **剪贴板内容隐私**：preview_50chars 可能泄露密码/敏感信息 → 实现时加正则过滤（含 `password`/`key`/`token` 等关键词的不发送给 LLM）
- **屏幕 OCR 成本**：视觉 LLM 调用贵且慢，默认关，开时只截当前窗口区域不截全屏
- **LLM 决策拟人度**：temperature=0.8 可能导致红莉栖语气漂移 → 实施期录制 50 次决策样本，由用户评估语气一致性
- **节流参数调优**：5min/10min/30次/日 这些参数是初值，需用户实际使用 1-2 周后调
