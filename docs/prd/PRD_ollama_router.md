# PRD：轻量级自动路由层（Ollama 本地小模型分流）

## 1. 背景与目标

当前 [backend_router.py](../core/backend_router.py) 的 `route_and_send` 支持手动单选后端（chat / harness / hermes / deepseek / codex），另有 `auto` 模式通过远程 DeepSeek 做 chat/agent/gui 三分，但 agent/gui 实际仍走本地直连，并未真正分流到 harness 等 agent 后端。

本功能新增一个**轻量级路由层**，用一个**本地小模型（Ollama，qwen2.5:0.5b）**判断用户输入应交给哪个后端。范围收窄为**二分类**：本地直连（local）与 DeepSeek Harness（harness），本地直连作为兜底。

目标：
- 自动判断「需要桌面/GUI 操作」与「需要复杂代码/工程任务」两类意图，分别分流到本地直连与 harness。
- 分流模型本地运行，不依赖远端分类接口。
- 设置页新增「自动模式」独立开关 + 模式勾选 + Ollama 配置。

## 2. 能力边界（分流判据依据）

依据 [data/harness/cordis.full.yml](../data/harness/cordis.full.yml) 与 [deepseek-harness-master/docs/tool-catalog.md](../deepseek-harness-master/docs/tool-catalog.md)。

### 2.1 DeepSeek Harness 具备
- 代码/命令：`bash`、`subprocess`、`edit/read/write`、`glob/grep`、`str_replace_editor`
- 编排/子代理：`subagent`、`subagent_fork`、`send_message`、`workflow`、`todo_write`、`run_code`
- 搜索：`web_search`（`web_fetch` 被禁 `fetch:false` 防 SSRF）

### 2.2 DeepSeek Harness 缺失（关键缺口）
- 无任何 GUI/桌面能力：无 `open_target`（打开浏览器/文件）、无 `click/type_text/press_keys/focus_window`、无 `capture_screen/list_windows/read_clipboard`、无 `operate_gui`
- `terminal_*` 在 Windows 被禁用

### 2.3 本地直连（[desktop_tools.py](../core/desktop_tools.py)）具备
- 全部桌面能力：`open_target`（打开浏览器/文件/URL）、`operate_gui`、`click`、`type_text`、`press_keys`、`focus_window`、`capture_screen`、`list_windows`、`read_clipboard`
- 命令/文件/搜索：`run_command`、`web_search`、`fetch_url`、`file_find`、`list_dir`、`read_file`、`write_file`

### 2.4 分流判据

| 用户意图 | 分流目标 |
|---|---|
| 打开/操作应用、浏览器、点击、输入、截图、窗口管理 | 本地直连（local） |
| 复杂代码/工程任务（多步、子代理、文件编辑、bash、工作流） | deepseek-harness（harness） |
| 普通闲聊、简单问答、分类不确定 | 本地直连兜底（local） |

## 3. 功能需求

1. 设置页 Agent tab 新增「自动分流」独立开关（默认关闭），与现有「Agent 模式」下拉解耦。
2. 开关开启时展开：
   - 模式勾选：本地直连（local）、DeepSeek Harness（harness）两个复选项，默认都勾选。
   - Ollama 配置：Base URL（默认 `http://127.0.0.1:11434`）、Model（默认 `qwen2.5:0.5b`）。
3. 开启自动分流后，发送消息时优先用 Ollama 小模型分类，覆盖「Agent 模式」下拉的手动选择；关闭时恢复手动下拉行为。
4. 分类结果映射：`local → chat（本地直连）`，`harness → harness`。

## 4. 架构设计

### 4.1 新增 [core/ollama_router.py](../core/ollama_router.py)

暴露函数：

```
route_with_ollama(text: str, *, targets: list[str], base_url: str, model: str, timeout: float) -> str
```

- 调用 Ollama `POST {base_url}/api/chat`（`stream: false`）。
- 返回 `"local"` 或 `"harness"` 之一。
- 任何异常（连接失败、超时、非法返回）一律回退 `"local"`。

分类 prompt（few-shot + 二分类）：

```
你是路由分类器，判断用户消息该由哪个后端处理，只输出一个词，不要解释。
- local：需要操作桌面 GUI（打开/关闭应用或浏览器、点击、输入文字、截图、窗口管理、鼠标键盘），或普通闲聊、简单问答。
- harness：需要复杂代码/工程任务（编写或修改代码、运行命令、文件编辑、多步骤编程、子代理/工作流）。
输出只能是 local 或 harness。
```

### 4.2 改造 [backend_router.py](../core/backend_router.py)

在 `route_and_send` 的 mode 分发前插入自动分流分支：

- 读取 `router["auto_route"]`（bool）。若为真且 `system_role != "companion"`，走自动分流。
- `targets` 取 `router["auto_targets"]`；若未勾选任何目标，直接回退本地直连。
- 若只勾选一个目标，直接走该目标，无需调 Ollama。
- 勾选多个目标时调 `route_with_ollama`，得到 `local`/`harness` 后落入既有 `route == "harness"` 分支或末尾本地直连分支。

### 4.3 配置扩展 [config.py](../config.py)

`AGENT_ROUTER_DEFAULTS` 增加：

```python
"auto_route": False,                      # 自动分流开关（独立于 mode）
"auto_targets": ["local", "harness"],     # 勾选参与分流的模式
"ollama": {
    "base_url": "http://127.0.0.1:11434",
    "model": "qwen2.5:0.5b",
    "timeout": 30,
},
```

### 4.4 设置页改造 [ui/settings_dialog.py](../ui/settings_dialog.py)

在 Agent tab 的「Agent 模式」下拉之后、各后端配置块之前，新增：
- `QCheckBox`「自动分流（Ollama 小模型）」，绑定 `auto_route`。
- 自动分流勾选组（仅开关开启时可见）：`本地直连`、`DeepSeek Harness` 两个 `QCheckBox`，绑定 `auto_targets`。
- Ollama 配置：`Base URL`、`Model` 两个 `QLineEdit`，绑定 `ollama.base_url` / `ollama.model`。
- `_save` 写回 `agent_router.auto_route`、`agent_router.auto_targets`、`agent_router.ollama`。

## 5. 错误处理

- Ollama 不可达 / 未安装 / 超时 / 返回非预期值 → 回退 `local`（本地直连），并在状态栏提示「自动分流不可用，已回退本地直连」。
- `auto_targets` 为空 → 回退本地直连。
- 自动分流仅作用于普通对话（`system_role != "companion"`），companion 仍固定走本地直连。

## 6. 验收标准

1. 设置页勾选「自动分流」，仅勾选「本地直连」时，任意消息走本地直连，不调 Ollama。
2. 同时勾选「本地直连」与「DeepSeek Harness」时：
   - 输入「帮我打开浏览器/打开记事本/点击桌面」→ 走本地直连（local）。
   - 输入「帮我写个 Python 脚本 / 读取这个文件并修改 / 运行命令」→ 走 harness。
   - 输入「你好 / 谢谢」→ 走本地直连兜底（local）。
3. 关闭 Ollama 服务后发消息，自动分流回退本地直连，不崩溃。
4. 关闭「自动分流」开关后，恢复按「Agent 模式」下拉手动选择。
