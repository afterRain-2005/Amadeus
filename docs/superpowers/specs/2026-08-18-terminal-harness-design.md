# Amadeus Terminal → DeepSeek Harness 功能对齐设计

- 日期：2026-08-18
- 状态：已批准（一次全做；diff 走 Python 端计算）
- 约束：UI 设计不变（保留现有 AgentTerminal 的 CRT 风格）

## 目标

把独立终端窗口 `AgentTerminal` 的功能对齐到 DeepSeek Harness 的能力，补齐 5 项：

1. 命令历史 + Tab 补全
2. 工具调用可视化
3. 命令执行（走 harness bash 工具）
4. diff 查看
5. 中断 + 审批内联

## 架构决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 命令执行 | harness bash 工具 | 用户指定；bash 工具已挂在 cordis 核心区 |
| diff 查看 | Python 端从编辑工具事件计算 | 无需重建闭包；从 `str-replace-editor` 的 tool/call args + tool/result 计算 old/new |
| 审批回传 | 扩展 SDK（Node server + Python client） | Node 侧 `transport.request()` 与 Python `next_request()/respond()` 双向通道已具备，只是未接通 |
| 中断 | 扩展 Node server 加 interrupt method | SDK 当前无中断方法；bash 的 AbortSignal 是命令级中断基础 |

## 关键事实（功能层）

- harness bash 工具是**同步完整输出**（`ctx.shell.run()` 返回完整 stdout/stderr/exitCode/signal），非逐字流式；PTY 在 Windows 被禁用。
- SDK 事件流已透出 `tool/start`、`tool/call`、`tool/result` 等（经 `session.event`），当前 harness_bridge 仅降级为 `on_status` 文本。
- 改 Node `server.ts` 后必须重新 `pnpm build` 闭包（`packaged-bin.js`）才生效。

## 实施阶段（顺序执行，最终统一验证）

### 阶段 A — 纯 Python/Qt（不重建闭包）

- A1 命令历史 + Tab 补全（AgentTerminal）
- A2 工具调用可视化 + 命令执行 + diff（harness_bridge 结构化事件 → Terminal 卡片）

### 阶段 B — 扩展 SDK + 重建闭包

- B1 审批内联（Node server 注册 approval answerer + Python client 回传）
- B2 中断（Node server interrupt method）

## 关键文件

- `desktop_pet.py` — AgentTerminal / PetWindow（UI，不动样式）
- `core/harness_bridge.py` — harness 事件流处理（扩展结构化回调）
- `core/backend_router.py` — 路由（harness turn 调用点）
- `deepseek-harness-master/packages/sdk/server/src/server.ts` — Node SDK server（审批/interrupt 扩展）
- `deepseek-harness-master/python/sdk/src/deepseek_harness/client.py` — Python client（request/response 通道）
