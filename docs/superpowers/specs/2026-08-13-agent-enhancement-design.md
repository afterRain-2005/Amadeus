# Amadeus Agent 增强 设计文档

> 日期：2026-08-13
> 状态：设计已与用户逐段确认，待用户最终审阅
> 定位：增强已有 agent（非重建），补工具缺口 + 接 OpenClaw 重型 CUA + 速度体感打磨
> 上游：[2026-08-13-amadeus-productization-design.md](2026-08-13-amadeus-productization-design.md)（产品化总 spec）

---

## 1. 背景与现状

用户提出 4 项新功能：① TTS ② OpenClaw ③ 类似 Hermes 的记忆 ④ agent 生态 + 响应速度。
经分解，本 spec 只覆盖 **② OpenClaw + ④ agent 生态/速度** 这一 track（TTS 与记忆为独立 track，后续单独 brainstorm）。

### 关键发现：agent 基础设施已存在且成熟

代码审查发现，用户选择的若干决策**已实现**，本 spec 不重建这些：

| 决策 | 现状 | 位置 |
|---|---|---|
| function-calling agent loop | ✅ `run_local_run` 完整实现：流式 LLM + `tool_calls` 解析 + 结果回喂 + 10 轮上限 | [core/agent_client.py:111](../../../core/agent_client.py#L111) |
| C 分级控制 | ✅ `auto_allow_tools` 白名单(只读)自动放行 / `CONFIRMATION_REQUIRED` 弹确认 / session+always 记忆 | [core/agent_client.py:178-229](../../../core/agent_client.py#L178-L229) |
| 即时确认 | ✅ `_send` 里 `让我想想…` 瞬间显示 | [desktop_pet.py:803](../../../desktop_pet.py#L803) |
| Hermes 可选高级 + 直连默认 | ✅ `run_hermes_run` + `run_local_run` 双模式 | [core/agent_client.py](../../../core/agent_client.py) |

**已有 9 个工具**（[core/desktop_tools.py](../../../core/desktop_tools.py)）：
`capture_screen` / `list_windows` / `read_clipboard` / `open_target` / `focus_window` / `type_text` / `press_keys` / `click` / `run_command`

覆盖：系统操作 ✓、开发辅助 ✓、基础视觉 CUA（截图+坐标点击+输入） ✓。

### 真实差距

| 类别 | 现状 | 缺口 |
|---|---|---|
| 信息查询 | ❌ 无 | `web_search` / `fetch_url` |
| 文件管理 | ⚠️ 只能靠 `run_command` 跑 PowerShell | 专用 `file_find` / `read_file` / `write_file` / `list_dir` |
| OpenClaw | ⚠️ 手搓 CUA（截图+坐标点击）对复杂界面脆 | OpenClaw 作重型 CUA 工具，复杂视觉 GUI 时调起 |
| 响应速度 | ⚠️ 有"让我想想…"但静态死等首 token | Live2D 即时倾听态 + 呼吸动画贯穿 + 工具进度反馈 |

## 2. 设计决策（用户逐项确认）

| 决策点 | 选择 | 含义 |
|---|---|---|
| Agent 路径 | C 混合 | 默认自建轻量 agent loop（已存在），OpenClaw 作可选重型 CUA 工具按需调起 |
| 能力范围 | 信息/系统/文件/开发 四类 | 轻量 loop 覆盖四类工具；系统/开发已有，补信息+文件 |
| OpenClaw 场景 | 仅视觉 GUI 操作 | 需要看屏操作任意 GUI（填表单/点菜单/无 CLI 软件）时才调起 |
| 控制模型 | C 分级 | 只读自动放行 / 写·执行弹确认 / 破坏类默认关（**已实现**，新工具按此分级） |
| Agent loop 架构 | 方案1 function-calling | **已实现**，沿用 `run_local_run` |
| 速度策略 | 即时物理反馈 + 动效填补 | Live2D 即时倾听态 + 呼吸动画 + 工具进度分段 |

## 3. 工具缺口补齐

在 [core/desktop_tools.py](../../../core/desktop_tools.py) 的 `TOOL_DEFINITIONS` 与 `execute_tool` 中新增 6 个工具。

### 3.1 信息查询工具（只读，自动放行）

**`web_search(query: str)`**
- 实现：`ddgs` 库（DuckDuckGo Search，免费无 API key）执行搜索，返回前 5 条 `{title, snippet, url}`
- 输出文本格式：`1. {title}\n   {snippet}\n   {url}` × 5
- 风险：只读 → 加入 `auto_allow_tools` 策略白名单

**`fetch_url(url: str)`**
- 实现：`httpx` 抓取页面 → `trafilatura` 提取正文 → 截断 8000 字符
- 失败处理：HTTP 错误/超时返回 `{"text": "Fetch failed: {error}"}`
- 风险：只读 → 自动放行
- 安全：仅允许 http/https；限制响应体 2MB

### 3.2 文件管理工具

**`file_find(pattern: str, root: str = "桌面")`**
- 实现：`pathlib.Path.glob` 递归匹配，返回最多 30 条路径
- `root` 默认用户桌面；支持 `~` 展开
- 风险：只读 → 自动放行

**`list_dir(path: str)`**
- 实现：列目录条目（名称+大小+类型），最多 100 条
- 风险：只读 → 自动放行

**`read_file(path: str)`**
- 实现：读文本文件，限 20000 字符；拒绝 >2MB 或二进制（非 UTF-8 可解码）
- 风险：只读 → 自动放行
- 安全：路径校验（`resolve()` 后在允许根目录内，防 `../` 越界）

**`write_file(path: str, content: str)`**
- 实现：写文本文件（覆盖）
- 风险：写操作 → 加入 `CONFIRMATION_REQUIRED`
- 安全：同路径校验；拒绝写系统目录（`C:\Windows` 等）

### 3.3 风险分级落点

更新 [config.py](../../../config.py) `APPROVAL_POLICY`：
- `auto_allow_tools` 增加：`web_search`, `fetch_url`, `file_find`, `list_dir`, `read_file`
- `CONFIRMATION_REQUIRED` 增加：`write_file`

## 4. OpenClaw 重型 CUA 工具

### 4.1 工具契约

注册新工具 `operate_gui(task: str)`：
- **输入**：自然语言任务描述（如"在浏览器打开 GitHub 并登录，账号在剪贴板"）
- **输出**：任务执行结果摘要 + 关键截图（若支持）
- **风险**：加入 `CONFIRMATION_REQUIRED`（视觉操作电脑=高风险，每次确认）

### 4.2 触发逻辑

由 LLM 在 function-calling 循环中自主决定：
- 简单单次操作（点某坐标、输入一段字、切窗口）→ 用现有 `click`/`type_text`/`focus_window`
- 复杂视觉 GUI 操作（多步、需识别界面元素、填表单、操作无 CLI 软件）→ 调 `operate_gui`

LLM 的工具描述里明确指引此分工，避免 OpenClaw 滥用（贵且慢）。

### 4.3 实现机制（待定）

OpenClaw 的确切调用接口（subprocess / SDK / HTTP API）**待实现时查 OpenClaw 仓库文档确定**。本 spec 先定契约：
- `operate_gui` 内部启动 OpenClaw 执行 task，流式监控进度（经 `on_status` 反馈），完成返回摘要
- 超时上限 120s；用户可经确认弹窗的"拒绝"中断

**前置条件**：用户需先本地部署 OpenClaw（用户有 GPU）。若 OpenClaw 未部署，`operate_gui` 返回 `{"text": "OpenClaw 未部署，无法执行视觉操作"}`，agent 回退到手搓 CUA 或告知用户。

### 4.4 降级

- OpenClaw 不可用 → `operate_gui` 返回错误文本 → LLM 自行回退到 `click`/`type_text`/`capture_screen` 手搓方案
- 与现有"Hermes 不可用回退直连"同款降级哲学

## 5. 响应速度体感打磨

根因：`让我想想…` 是静态文本，LLM 首 token 前有"死等"空窗，体感卡。LLM 首 token 延迟是网络/模型固有，无法消除，靠感知手段填补。

### 5.1 Live2D 即时倾听态

`_send` 瞬间（[desktop_pet.py:803](../../../desktop_pet.py#L803) 处）立即经 `pet_command.json` 发 `emotion: "thinking"`，Live2D 切思考/倾听表情，不等 LLM。
- 角色"物理反应"先于文字反应，填补首 token 空窗

### 5.2 呼吸动画贯穿等待

把 `_send` 的静态 `让我想想…` 换成调用 `_show_thinking_dots()`（[desktop_pet.py:580](../../../desktop_pet.py#L580) 已有的"● ● ●"呼吸动画），从发送瞬间就呼吸，不等首个 delta。
- 现有 `_show_thinking_dots` 是 delta 时触发；改为 `_send` 也触发，统一等待态视觉

### 5.3 工具进度分段反馈

工具执行时气泡显示带图标+过渡的进度文案：
- 现有 `on_status`（[_show_status](../../../desktop_pet.py#L845)）已通路，增强 `_status_text`（[agent_client.py:90](../../../core/agent_client.py#L90)）文案为带图标分段：
  - `🔍 搜索：{query}` → `✓ 搜索完成`
  - `📄 读取：{filename}` → `✓ 读取完成`
  - `🖱 操作 GUI：{task}` → `✓ 操作完成`
- 配合现有呼吸动画，工具执行期间视觉持续活跃

## 6. 安全与鲁棒性

- **路径校验**：所有文件工具 `resolve()` 后校验在允许根（用户目录/桌面/项目目录）内，防 `../` 越界
- **大小限制**：`read_file` ≤2MB/20000 字符；`fetch_url` ≤2MB 响应体；`run_command` stdout ≤12000 字符（现有）
- **超时**：`fetch_url` 15s；`operate_gui` 120s；`run_command` 45s（现有）
- **破坏类默认关**：`write_file` 需确认；删除/系统关机类不提供专用工具（必须经 `run_command` 且受 `CONFIRMATION_REQUIRED` + 命令前缀白名单双重约束）
- **降级链**：OpenClaw 不可用 → 手搓 CUA；工具异常 → 返回错误文本，不崩 agent loop

## 7. 测试策略

- **工具单元测试**：每个新工具 mock 外部依赖（ddgs/httpx/文件系统）测正常+异常路径
- **路径校验测试**：`../` 越界、系统目录、符号链接等攻击用例
- **分级测试**：新工具的 `auto_allow` / `CONFIRMATION_REQUIRED` 归类正确
- **Agent loop 集成**：mock LLM 返回 `web_search` tool_call → 验证执行+回喂+最终回复
- **OpenClaw 降级**：mock OpenClaw 不可用 → 验证回退手搓 CUA
- **手动验收**：发"帮我搜下今天上海天气"→ 见搜索进度气泡→见结果；发"打开记事本写一句话"→ 见确认弹窗

## 8. 不做（YAGNI）

- ❌ 多 agent 编排/任务队列（单 function-calling loop 够用）
- ❌ 工具插件市场/动态加载
- ❌ 自动学习新工具
- ❌ OpenClaw 深度定制训练（用其默认能力）
- ❌ 持久化后台任务（同步执行即可）
- ❌ 重写现有 9 个工具（它们工作正常）

## 9. 待定 / 风险

| 项 | 说明 | 处理 |
|---|---|---|
| OpenClaw 调用接口 | subprocess / SDK / HTTP API 未确定 | 实现时查 OpenClaw 仓库文档；spec 已定输入输出契约 |
| OpenClaw 本地部署 | 需用户先部署 OpenClaw + GPU | 实现前确认部署；未部署则降级 |
| `ddgs` 可用性 | DuckDuckGo 偶有限流 | 失败返回错误文本，agent 改用 `run_command`+`curl` 兜底 |
| function-calling 支持 | 用户具体 API 端点是否支持 tools | 现有 `run_local_run` 已用 tools，说明已支持；新工具仅扩 schema |
| Live2D emotion 通路 | `pet_command.json` 轮询延迟 | 属 P0 IPC 重构范围；本 track 用现有通路，P0 完成后提速 |

## 10. 与路线图关系

- 本 track = 产品化 spec 的"对话层增强"，不改变 P0-P4 路线图
- P0 IPC 重构（文件轮询→进程内信号）会进一步提速 Live2D emotion 通路（§5.1）
- 记忆 track（P1）独立，会增强 agent 的记忆注入（现有 `memories` 来自正则，P1 升级为 SQLite+向量）
- TTS track 独立
