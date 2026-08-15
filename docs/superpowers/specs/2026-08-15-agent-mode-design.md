# Agent 模式（deepseek/Hermes + codex 双后端 + gate 分流）设计

日期：2026-08-15
状态：已获用户批准（方案 A 务实双栈）

## 1. 背景与目标

需求文档（六系统：agent / companion / voice call / gate / claw / research）中 agent 系统要求：

1. **agent 模式**提供两种可选引擎：
   - **deepseek 模式**：后台 harness 进程 —— 经用户确认为 **Hermes 网关进程**（`hermes -p kurisu gateway`，OpenAI 兼容 API server，8642 端口）
   - **codex 模式**：codex 后台进程作为转发层（amadeus → codex exec 子进程），保持角色一致性
2. **gate 自动分流**：按输入自动路由到 agent 模式 / 聊天系统 / openclaw

本轮范围 = agent 双后端 + gate（用户跳过范围缩减提问，按需求文档全量执行）。

## 2. 现状与差距（2026-08-15 实测）

| 项 | 状态 | 依据 |
|---|---|---|
| `run_local_run`（直连 DeepSeek + 本地工具 + 审批） | 已运行，桌宠唯一路径 | desktop_pet.py:237 |
| `run_hermes_run`（/v1/runs + SSE + approval + stop 客户端） | 已写好，**未接线**，缺 Bearer 认证 | core/agent_client.py:267 |
| `HERMES_DEFAULTS`（enabled/base_url/profile/session_id） | 已有，缺 `api_server_key` | config.py:35 |
| Hermes v0.20.0 环境 | 已装；kurisu profile 存在（SOUL.md 2821B + .env: API_SERVER 8642 + key） | `hermes --version` / profile 目录实测 |
| codex-cli 0.146.0 环境 | 已装，已登录（API key） | `codex login status` |
| Hermes API server 规格 | `hermes -p kurisu gateway` 暴露；system/instructions **叠加**在 SOUL.md 之上 | 官方 website/docs/user-guide/features/api-server.md |
| codex exec 规格 | `--json` JSONL 事件、`resume --last` 会话延续、`-C` 工作根目录加载 AGENTS.md、`-o` 末消息文件、`-s` 沙箱 | `codex exec --help` |

## 3. 总体架构

```
用户输入 (desktop_pet._send)
   │
   ▼
core/backend_router.py ── classify_input() ──► "chat" | "agent" | "gui"
   │                    L1 关键词规则 → L2 DeepSeek 意图分类（复用现有 key）
   ├─ chat ──► run_local_run        （现状路径，不动）
   ├─ agent ─► 所选 agent 后端
   │            ├─ hermes 模式 ──► run_hermes_run（接线 + 认证）
   │            └─ codex 模式 ──► core/codex_client.py（新增）
   └─ gui ───► openclaw operate_gui（现有 desktop_tools 机制；未启用则降级 agent）
```

- 模式为 `auto` 时才过 gate；`chat` / `hermes` / `codex` 固定模式直连。
- 三条后端链路统一回调接口：`on_delta(text)` / `on_status(text)` /（Hermex 路径另有 `on_approval(payload)`）。

## 4. 组件设计

### 4.1 backend_router（新增 `core/backend_router.py`）

- `classify_input(text: str, *, openclaw_enabled: bool) -> str`
  - **L1 规则（零成本先判）**：
    - gui 意图：`打开|关闭|点击|截屏|截图|操作.*窗口|鼠标|键盘` 且 openclaw_enabled
    - agent 意图：`搜索|查找文件|帮我(写|整理|运行|分析)|读.*文件|列出|下载|运行命令|查一下|百度|google`
    - chat 意图：短句（≤6 字）问候/感叹、无动作动词
  - **L2 DeepSeek 意图分类**：L1 不确定时，非流式小请求（max_tokens ≤ 100，
    JSON 输出 `{"route": "chat|agent|gui", "reason": "..."}`），复用 data/config.json 的
    api_key/endpoint/model。
  - **任何失败默认 `chat`**（最安全路径）。
- `route_and_send(...)`：按设置分发到对应后端函数，异常时走降级链（§6）。

### 4.2 Hermes 模式（deepseek 模式）

- **认证**：`HERMES_DEFAULTS` 新增 `api_server_key`；优先从
  `~/.hermes/profiles/<profile>/.env` 的 `API_SERVER_KEY=` 读取（setup 脚本/
  首次启动自动同步），Bearer 头带上。
- **生命周期**：
  - 发送前 `GET /health`（2s 超时）探活；
  - 不通 → `subprocess.Popen(["hermes", "-p", profile, "gateway"],
    creationflags=DETACHED|CREATE_NEW_PROCESS_GROUP, stdout/stderr →
    data/hermes_gateway.log)`，轮询 `/health` 最多 30s；
  - 仍失败 → 降级 `run_local_run`，气泡提示"Hermes 不可用，已切本地直连"；
  - **桌宠退出不杀网关进程**（常驻，同 GPT-SoVITS 惯例；下次启动探活直通过）。
- **角色一致性**：SOUL.md（profile 侧）+ `instructions=KURISU_OUTPUT_FORMAT`（amadeus 侧），
  依据官方 System Prompt Handling 层叠语义。
- **会话**：请求头 `X-Hermes-Session-Id: amadeus-kurisu` + body 传最近 14 条
  conversation_history；approval 走现有 on_approval 弹窗 → POST approval。

### 4.3 codex 模式（新增 `core/codex_client.py`）

- `run_codex_turn(*, input_text, workspace, sandbox, timeout, on_delta, on_status) -> str`
- **调用形态**：
  - 首轮：`codex exec --json --skip-git-repo-check -s <sandbox> -C <workspace> -o data/codex_last.txt "<input>"`
  - 后续：同上 + `resume --last`（延续最近会话，保证多轮上下文）
- **角色一致性**：`<workspace>/AGENTS.md`（= `data/codex_workspace/AGENTS.md`），由
  setup 逻辑从 SOUL.md + KURISU_OUTPUT_FORMAT 生成；`-C` 使 codex 自动加载为
  developer instructions。
- **流式**：后台线程逐行读 stdout JSONL，事件适配层把 `agent_message` 文本 /
  命令执行事件映射到 on_delta / on_status；**最终回复以 `-o` 产物文件为兜底真相**
  （隔离 codex 版本间 JSONL 事件格式差异）。
- **沙箱**：默认 `read-only`（写操作需求走 Hermes/本地路径）；设置页可改
  `workspace-write`。
- **取消/超时**：默认 120s；超时或用户停止 → `terminate()` 子进程。
- **失败**：退出码非 0 → 抛 RuntimeError（气泡报错，不自动重试）。

### 4.4 配置与设置 UI

- `config.py`：
  - `HERMES_DEFAULTS` += `api_server_key: ""`
  - 新增 `AGENT_ROUTER_DEFAULTS = {"mode": "chat", "codex": {"workspace": "data/codex_workspace", "sandbox": "read-only", "timeout": 120}}`
    （mode ∈ `chat | hermes | codex | auto`，默认 `chat` 保持现状行为）
- 设置页新增 agent tab：
  - 模式下拉（auto/chat/hermes/codex）
  - Hermes 状态灯（`/health` 实时探测）+ codex 可用性检测（`codex --version`）
  - codex 沙箱下拉（read-only / workspace-write）

## 5. 数据流与状态

- `_send` → `route_and_send` → 后端返回文本 → 现有 emotion_parser → 气泡 + TTS
  （不改变现有表达链路）。
- codex 会话标识：`resume --last` 依赖 codex 自身 session 存储（`~/.codex/sessions`），
  amadeus 不额外管理。

## 6. 错误处理与降级链

| 故障 | 行为 |
|---|---|
| Hermes /health 探活失败且拉起超时 | 降级 run_local_run + 气泡提示 |
| Hermes run 中途失败（run.failed/HTTP 错） | 同上降级（本轮输入直发 local） |
| codex 超时/非 0 退出 | 气泡报错，不自动重试 |
| gate L2 分类失败 | 默认 chat |
| openclaw 未启用但输入为 gui 意图 | 降级 agent 路径 |

## 7. 测试策略

- **单测**（不打真 API）：
  - `classify_input` 规则矩阵（纯函数）
  - codex JSONL 解析：录制真实 `codex exec --json` 输出做 fixture
  - Hermes 探活/拉起逻辑：mock httpx + Popen
  - route_and_send 分发矩阵（各模式 × 各后端异常）
- **手动验收**：
  1. hermes 模式真机一轮（网关自动拉起 → SSE 回复 → 角色语气符合 SOUL.md）
  2. codex 模式真机一轮（AGENTS.md 生效 → 多轮 resume 上下文延续）
  3. auto 模式三路各一例（闲聊→chat、搜索→agent、截屏→gui/openclaw）
  4. 降级：停网关且 hermes 模式发消息 → 本地直连提示

## 8. 环境前置（实施期确认）

- Hermes kurisu profile 的底层模型需指向 DeepSeek（`hermes -p kurisu model` 配置；
  profile `.api_key` 的 provider 归属在实施时核实——机器级 `hermes status` 显示
  所有 provider key 均未配置）。
- 首次启动自动同步 `API_SERVER_KEY`（profile .env → config）。

## 9. YAGNI（明确不做）

- ACP 统一协议客户端
- codex approval UI 转发（read-only 沙箱下无审批场景）
- Hermes Jobs/Sessions REST 全量封装（只用 /v1/runs + /health）
- 网关/codex 自动安装
- 多 agent 并发调度
