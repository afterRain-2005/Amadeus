# PRD：DeepSeek Harness 设置页补齐 + 全量功能启用 + 工作区修正

## 1. 背景与目标

### 1.1 现状
- 上一轮已把 DeepSeek Harness 接入为 Amadeus 的 agent 后端（`agent_router.mode = "harness"`），端到端验证通过（真实 key + `deepseek-chat` 返回 `'你好'`）。
- 但 harness 侧配置**严重残缺**，用户可感知的问题有两个：
  1. **设置页只有 3 个字段**（Agent 模式、Provider、Runtime Bin），`model / base_url / api_key` 未单独暴露，静默复用全局值；工作区、插件、Agent 预设等完全没有 UI 入口。
  2. **工作区硬编码**：`harness_bridge.py` 用 `cwd=str(Path.cwd())`，frozen exe 下工作目录会漂移，`session_root` 完全未传，走了 SDK 默认值。
- 更深层的问题：当前 SDK 用的默认 [cordis.yml](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/deepseek-harness-master/python/sdk-runtime/src/deepseek_harness_runtime/runtime/cordis.yml) 只挂 **8 个插件**，harness 实际拥有的 100+ 插件能力（web 搜索、skill、goal、plan-mode、workflow、subagent fork、sandbox、审批、持久终端、命令、钩子等）**全部未启用**。

### 1.2 目标
1. **设置页补全 harness 配置**：分组暴露「通用/连接」「工作区」「运行时」「功能开关」「安全」五类配置，替代当前 3 字段。
2. **启用 harness 全部功能**：内置一份完整版 `cordis.full.yml`（基于 dsh-base 全家桶），把 harness 能力从 8 插件提升到 40+ 插件。
3. **修正工作区**：`cwd` 与 `session_root` 显式可配置，默认落到 `data/harness/` 下，不再硬编码 `Path.cwd()`。

### 1.3 非目标
- 不改 harness 自身源码（不做 TS 插件开发；本轮是"配置编排 + UI + 桥接"，不是"魔改 harness"）。
- 不改主页面与 Terminal 的既有 UI 设计（前端壳子保持不变）。
- 不做 provider auto 切换（用户手动选 harness / 直连）。
- 不替换现有自研 agent 系统文件（保留 `core/agent_client.py` 等，仅不启用）。

### 1.4 关键结论（调研依据）
- harness 的全部插件**代码已经在 node closure 里**（[package.json](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/deepseek-harness-master/python/sdk-runtime/package.json) 依赖闭包含 100+ `@deepseek-ai/*` 包）。
- "启用全部功能"本质是**换一份完整的 cordis.yml**，不是重新 build、不是抄代码。
- 权威参照两份：
  - [examples/jsonrpc-agent/cordis.yml](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/deepseek-harness-master/examples/jsonrpc-agent/cordis.yml)（SDK 场景权威最小配置，已验证可跑）
  - [packages/bundle/base/cordis.patch.yml](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/deepseek-harness-master/packages/bundle/base/cordis.patch.yml)（dsh-base 全家桶的插件名 + config + 平台分支）

## 2. 架构设计

### 2.1 整体数据流

```
[settings_dialog.py] ──保存──▶ data/config.json (harness 子配置块)
                                    │
                                    ▼
[backend_router.py] ──读取 config ──▶ 组装 DeepSeekHarnessConfig
                                    │  (provider/model/base_url/api_key/cwd/session_root/cordis)
                                    ▼
[harness_bridge.py] ──启动──▶ node closure + cordis.full.yml
                                    │  (DSH_CWD / DSH_SESSION_ROOT / DEEPSEEK_API_KEY 透传)
                                    ▼
                          DeepSeek Harness runtime (40+ 插件)
```

### 2.2 完整 cordis.yml 方案（核心）

新文件：`deepseek-harness-master/python/sdk-runtime/src/deepseek_harness_runtime/runtime/cordis.full.yml`

设计原则：
1. **保留 SDK 必需 entry**：`sdk-jsonrpc-server`（JSON-RPC 服务）+ `agent-spine-demo`（SDK 场景的核心 spine，内置 skill/goal/jobs/system-prompt/tools/agent-loop）。
2. **agent-spine config 全开**：把上一版显式关闭的 `skills / toolJobs / workspaceContext / goals` 全部打开。
3. **追加 agent-spine 未内置的插件**：web / plan / workflow / editor / sandbox / approval / permission / subagent-fork / session-query / commands / timeout / reminder 等。
4. **平台分支**：Windows 禁用的插件用 `disabled: !!js process.platform === 'win32'` 标记（沿用 dsh-base 的做法），保证同一份配置跨平台可用。

完整插件清单见「附录 A：cordis.full.yml」。

### 2.3 设置页映射

| 分组 | 配置项 | 落盘字段 |
|---|---|---|
| 通用/连接 | provider、model、base_url、api_key | `harness.provider/model/base_url/api_key` |
| 工作区 | cwd（agent 工作根）、session_root（会话持久化根） | `harness.cwd/session_root` |
| 运行时 | runtime_bin、cordis 路径、request_timeout | `harness.runtime_bin/cordis/request_timeout_seconds` |
| 功能开关 | 见 3.4 清单 | 生成 cordis.full.yml 的插件开关 |
| 安全 | 沙箱模式、审批策略 | `harness.sandbox_mode/approval_policy` |

## 3. 详细设计

### 3.1 `config.py` — 扩充 harness 配置块

```python
HARNESS_DEFAULTS: dict[str, object] = {
    "provider": "deepseek-official",
    "model": "deepseek-chat",
    "base_url": "",                    # 空则复用全局 endpoint
    "api_key": "",                     # 空则复用全局 api_key
    "runtime_bin": "",                 # 空则自动定位
    "cordis": "",                      # 空则用内置 cordis.full.yml
    "cwd": "",                         # 空则用 data/harness/workspace
    "session_root": "",                # 空则用 data/harness/sessions
    "request_timeout_seconds": 300.0,
    "sandbox_mode": "ask",             # ask / auto / accept
    "approval_policy": "ask",          # ask / auto / accept
    # 功能开关（对应 cordis 插件，true=启用）
    "enable_web": True,
    "enable_plan_mode": True,
    "enable_workflow": True,
    "enable_editor": True,
    "enable_subagent_fork": True,
    "enable_sandbox": True,
    "enable_commands": True,
    "enable_terminal": False,          # Windows 默认关（PTY 限制）
}
```

### 3.2 `harness_bridge.py` — 消费完整配置 + 修正工作区

改动点：
1. `_default_cordis()` 优先返回 `cordis.full.yml`（相对 runtime 目录），不存在则回退原 `cordis.yml`。
2. `_resolve_cwd()` / `_resolve_session_root()`：优先读 config 的 `harness.cwd/session_root`，空则落到 `data/harness/workspace` / `data/harness/sessions`（`data/` 目录在 frozen 下取 exe 同级，非 `Path.cwd()`）。
3. 组装 `DeepSeekHarnessConfig` 时显式传入 `cwd / session_root / cordis / base_url / api_key / model / provider`，不再用 `Path.cwd()`。
4. 透传 env：`DSH_CWD`、`DSH_SESSION_ROOT`、`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`（沿用现有 `_run_via_sdk` 逻辑）。

### 3.3 `backend_router.py` — 读取完整 harness 配置

`model` 已改为 `harness_cfg.get("model")`，需同步补 `base_url / api_key / cwd / session_root / cordis` 的读取，并作为 kwargs 传给 harness_bridge。

### 3.4 `ui/settings_dialog.py` — 补全 harness 设置页

在「Agent」tab 下，把当前 3 字段扩展为分组表单：

```python
# 通用/连接
harness_form.addRow("Provider", self.harness_provider)   # deepseek-official / custom-openai
harness_form.addRow("Model", self.harness_model)         # 默认 deepseek-chat
harness_form.addRow("Base URL", self.harness_base_url)   # 空=复用全局
harness_form.addRow("API Key", self.harness_api_key)     # 密码框，空=复用全局

# 工作区
harness_form.addRow("工作区目录", self.harness_cwd)        # 默认 data/harness/workspace
harness_form.addRow("会话目录", self.harness_session_root) # 默认 data/harness/sessions

# 运行时
harness_form.addRow("Runtime Bin", self.harness_runtime_bin)
harness_form.addRow("Cordis 配置", self.harness_cordis)   # 空=内置 cordis.full.yml
harness_form.addRow("超时(秒)", self.harness_timeout)

# 功能开关（QCheckBox）
enable_web / enable_plan_mode / enable_workflow / enable_editor /
enable_subagent_fork / enable_sandbox / enable_commands / enable_terminal

# 安全
harness_form.addRow("沙箱模式", self.harness_sandbox_mode)   # ask/auto/accept
harness_form.addRow("审批策略", self.harness_approval_policy) # ask/auto/accept
```

保存逻辑把上述值写回 `config["harness"]`，再根据「功能开关」生成/覆盖 `data/harness/cordis.full.yml`（见 3.5）。

### 3.5 功能开关 → cordis 插件生成逻辑

设置页的开关映射到 cordis 插件段，保存时按开关组装 YAML（默认全开，terminal 例外）：

| 开关 | 插件段 |
|---|---|
| enable_web | web / web-search-deepseek / tool-web |
| enable_plan_mode | plan-mode |
| enable_workflow | workflow-worker-thread / tool-workflow |
| enable_editor | tool-str-replace-editor |
| enable_subagent_fork | subagent-fork-in-process / tool-subagent-fork |
| enable_sandbox | sandbox-local / sandbox-policy |
| enable_commands | commands / command-goal |
| enable_terminal | tool-bash-persistent / terminal / terminal-bash |

## 4. 依赖与打包

### 4.1 依赖
- 无新增 Python 依赖。
- 无新增 node 依赖（插件已在 closure 内）。

### 4.2 打包
- `Amadeus.spec` 的 `datas` 已包含整个 `deepseek_harness_runtime` 目录，新文件 `cordis.full.yml` 会自动被打包，无需改 spec。
- 需确认 `data/harness/` 目录在 frozen 下的读写权限（首次运行时自动 mkdir）。

## 5. 测试方案

### 5.1 启动验证（核心）
1. 开发模式跑 harness_bridge 的 `start/initialize`，确认 40+ 插件全部 load 成功、无 missing-dependency 报错。
2. 用真实 key 发一句话，确认 agent 仍能正常返回（回归验证）。
3. 重点验证新增插件是否被正确注册到 tool 列表（观察 `session/event` 里 `tool/call` 的 tool 名出现 web / todo / workflow 等）。

### 5.2 工作区验证
1. 设置页填工作区目录 → 保存 → 确认 `data/config.json` 写入。
2. 触发一次带文件操作的请求，确认 agent 在指定工作区读写，而非 `Path.cwd()`。

### 5.3 边界场景
- cordis.full.yml 缺失 → 回退原 cordis.yml 并提示。
- node runtime 不可用 → 回退 deepseek_client.py 直连（现有逻辑保留）。
- Windows 下 enable_terminal 打开 → 插件 disabled 不加载，不报错。

## 6. 风险与限制（诚实标注）

| 风险/限制 | 说明 | 处理 |
|---|---|---|
| terminal-bash PTY 在 Windows 不可用 | jsonrpc-agent README 明确"persistent PTY requires POSIX，非 Windows agent 接口" | 默认 `enable_terminal=false`，cordis 用 `disabled: win32` |
| hooks-codex/claude 需用户 hook 配置 | 无 config 时插件空跑（contained） | 本轮不暴露 hooks UI，后续按需 |
| web-search-exa/perplexity 需额外 key | deepseek 官方搜索用已有 DEEPSEEK key，够用 | 默认走 deepseek 搜索 |
| tool-ask-user 答案回传 | SDK JSON-RPC 是否支持 userQuestions 回传待验证 | **阶段 2 验证**，本轮不启用 |
| code-runtime run_code 工具暴露 | run_code 由 tool 暴露机制待验证 | **阶段 2 验证**，本轮不启用 |
| closure 缺少数个 dsh-base 包 | spill、tool-fs-search、skill-badge、telemetry、attachment-local、typert 等不在 SDK closure | 不挂（挂了会启动失败） |

## 7. 实施步骤

1. 新建 `cordis.full.yml`（附录 A 内容，逐项核对 config 后落盘）。
2. `config.py` 加 `HARNESS_DEFAULTS` 扩充块。
3. `harness_bridge.py` 改 `_default_cordis / cwd / session_root` 逻辑。
4. `backend_router.py` 读取完整 harness 配置。
5. `settings_dialog.py` 补全分组表单 + 保存逻辑 + cordis 生成逻辑。
6. 启动验证（5.1）+ 工作区验证（5.2）。
7. 重新打包 exe（0.6.0）。

## 8. 用户验证清单

- [ ] 设置页「Agent」tab 出现五组 harness 配置（通用/工作区/运行时/功能/安全）
- [ ] 工作区目录可配置，保存后 agent 在该目录读写
- [ ] 功能开关默认全开（terminal 除外），可逐个关闭
- [ ] 发消息后 agent 仍正常回复（回归）
- [ ] 请求涉及 web 搜索时，agent 能调用 web 工具
- [ ] 打包后 exe 启动默认配好完整 harness

## 附录 A：cordis.full.yml（目标结构）

> 实施时以 examples/jsonrpc-agent/cordis.yml + packages/bundle/base/cordis.patch.yml 为权威逐项核对 config，以下是目标插件编排（40+ 插件）。

```yaml
# Amadeus 完整版 DeepSeek Harness 配置
# stdout 保留给 JSON-RPC，不加载 console logger / Web UI / API gateway

# 1. SDK JSON-RPC 服务（必需）
- id: sdk-jsonrpc-server
  name: '@deepseek-ai/dsh-sdk-jsonrpc-server'

# 2. LLM 适配器
- id: llm-deepseek
  name: '@deepseek-ai/dsh-llm-deepseek'
  config: { thinking: enabled, reasoningEffort: max }
- id: llm-pi-ai
  name: '@deepseek-ai/dsh-llm-pi-ai'

# 3. 凭据 / 设置
- id: credentials
  name: '@deepseek-ai/dsh-credentials-local'
- id: settings
  name: '@deepseek-ai/dsh-settings-file'

# 4. Agent Spine（核心，全量开关）
- id: agent-spine
  name: '@deepseek-ai/dsh-agent-spine-demo'
  config:
    persona: !!js process.env.DSH_SYSTEM_PROMPT ?? ''
    workspaceContext: { maxBytes: 65536 }
    skills: { enabled: true }
    toolBash: { enableRunInBackground: false }
    toolJobs: {}
    goals: {}

# 5. 子进程 / 执行器
- id: subprocess
  name: '@deepseek-ai/dsh-subprocess-local'
- id: bash
  name: '@deepseek-ai/dsh-bash-local'
  config: { cwd: !!js process.env.DSH_CWD ?? process.cwd(), timeoutMs: 60000 }

# 6. 会话持久化 / 查询
- id: sessions
  name: '@deepseek-ai/dsh-session-persistence-jsonl'
  config: { root: !!js process.env.DSH_SESSION_ROOT ?? './.sessions', compression: 'gzip' }
- id: session-checkpoints
  name: '@deepseek-ai/dsh-session-checkpoint-policy'
- id: session-query-sqlite
  name: '@deepseek-ai/dsh-session-query-sqlite'
  config: { path: ':memory:', openAt: never }
- id: session-projection
  name: '@deepseek-ai/dsh-session-projection'

# 7. 文件系统工具
- id: fs-local
  name: '@deepseek-ai/dsh-fs-local'
  config: { cwd: !!js process.env.DSH_CWD ?? process.cwd() }
- id: fs-observation-policy
  name: '@deepseek-ai/dsh-fs-observation-policy'
- id: tool-fs
  name: '@deepseek-ai/dsh-tool-fs'

# 8. 沙箱 / 审批 / 权限
- id: sandbox
  name: '@deepseek-ai/dsh-sandbox-local'
- id: sandbox-policy
  name: '@deepseek-ai/dsh-sandbox-policy'
  config:
    mode: !!js process.env.DSH_PERMISSION_MODE ?? 'ask'
    workspaceRoot: !!js process.env.DSH_CWD ?? process.cwd()
- id: approval
  name: '@deepseek-ai/dsh-user-approval'
  config: { policy: !!js process.env.DSH_PERMISSION_MODE ?? 'ask' }
- id: permission
  name: '@deepseek-ai/dsh-permission-presets'

# 9. 子代理全家桶
- id: subagent
  name: '@deepseek-ai/dsh-subagent'
- id: subagent-spawn-in-process
  name: '@deepseek-ai/dsh-subagent-spawn-in-process'
  config: { providerName: spawn }
- id: subagent-fork-in-process
  name: '@deepseek-ai/dsh-subagent-fork-in-process'
  config: { providerName: fork }
- id: tool-subagent
  name: '@deepseek-ai/dsh-tool-subagent'
  config: { provider: spawn, toolName: subagent, enableRunInBackground: false }
- id: tool-subagent-fork
  name: '@deepseek-ai/dsh-tool-subagent'
  config: { provider: fork, toolName: subagent_fork, backgroundMode: one-shot }
- id: tool-subagent-control
  name: '@deepseek-ai/dsh-tool-subagent-control'

# 10. Todo / 计量 / 压缩
- id: tool-todo
  name: '@deepseek-ai/dsh-tool-todo'
  config: { allowParallelInProgress: true }
- id: token-meter
  name: '@deepseek-ai/dsh-token-meter'
- id: compaction-basic
  name: '@deepseek-ai/dsh-compaction-basic'
  config: { thresholdRatio: 0.8, retainRatio: 0.16, maxTokens: 8192, compactionRetries: 1 }
- id: tool-result-pruner
  name: '@deepseek-ai/dsh-compaction-tool-result-pruner'
  config: { thresholdChars: 50000, headChars: 4000, tailChars: 4000 }

# 11. Web 搜索 / 抓取
- id: web
  name: '@deepseek-ai/dsh-web'
  config: { searchProvider: deepseek-official }
- id: web-search-deepseek
  name: '@deepseek-ai/dsh-web-search-deepseek'
  config: { apiKeyEnv: DEEPSEEK_API_KEY }
- id: tool-web
  name: '@deepseek-ai/dsh-tool-web'
  config: { fetch: false, searchTimeoutMs: 60000 }

# 12. 计划模式 / 工作流
- id: plan-mode
  name: '@deepseek-ai/dsh-plan-mode'
- id: workflow-worker-thread
  name: '@deepseek-ai/dsh-workflow-worker-thread'
  config: { provider: spawn }
- id: tool-workflow
  name: '@deepseek-ai/dsh-tool-workflow'

# 13. 编辑器
- id: tool-str-replace-editor
  name: '@deepseek-ai/dsh-tool-str-replace-editor'
  config: { maxOutputChars: 16000 }

# 14. 命令
- id: commands
  name: '@deepseek-ai/dsh-commands'
- id: command-goal
  name: '@deepseek-ai/dsh-command-goal'

# 15. 超时 / 提醒
- id: timeout-policy
  name: '@deepseek-ai/dsh-tool-call-timeout-policy'
- id: repeat-tool-reminder
  name: '@deepseek-ai/dsh-repeat-tool-reminder'

# 16. 持久终端（Windows 禁用 PTY）
- id: tool-bash-persistent
  name: '@deepseek-ai/dsh-tool-bash-persistent'
  disabled: !!js process.platform === 'win32'
- id: terminal
  name: '@deepseek-ai/dsh-terminal'
  disabled: !!js process.platform === 'win32'
- id: terminal-bash
  name: '@deepseek-ai/dsh-terminal-bash'
  disabled: !!js process.platform === 'win32'
```

## 附录 B：阶段 2（本轮不做，验证后再说）

| 待验证项 | 需要做的事 |
|---|---|
| tool-ask-user 答案回传 | 确认 SDK JSON-RPC 是否暴露 userQuestions 回答通道；若无，需写 TS 插件桥接（进入"魔改 harness"范围） |
| code-runtime run_code | 确认 run_code 工具的暴露机制，再决定是否挂 code-runtime-worker-thread |
| hooks-codex/claude | 用户提供 hook 配置文件后再挂，否则空跑无意义 |
