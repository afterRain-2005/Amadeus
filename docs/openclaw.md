# OpenClaw 接入

amadeus-py 通过 OpenClaw Gateway 的 OpenAI 兼容 HTTP API 接入 [OpenClaw](https://docs.openclaw.ai/gateway)(Node.js 个人 AI 助理平台),客户端实现集中在 `core/openclaw_client.py`。

## 两条接入路径

| 路径 | 入口 | 说明 |
|------|------|------|
| 对话后端 | `agent_router.mode = "openclaw"` | 整轮对话委托给 OpenClaw 代理(skills/浏览器/CUA 由其 agent loop 处理),`message.delta` 流式回传 UI;失败降级本地直连 |
| CUA 工具 | `operate_gui` 工具(需审批) | 本地 agent 的 GUI 操作任务经 `/v1/chat/completions` 委托 OpenClaw 代理操作真实桌面,进度经 `on_status` 回传 |

## 配置(`data/config.json` 的 `openclaw` 键,默认值见 `config.py::OPENCLAW_DEFAULTS`)

| 键 | 默认 | 说明 |
|----|------|------|
| `enabled` | `false` | 启用 CUA 工具路径(关闭时 `operate_gui` 返回降级提示,gui 关键词路由也不生效) |
| `base_url` | `http://127.0.0.1:18789` | Gateway 地址(默认仅回环) |
| `token` | `""` | `OPENCLAW_GATEWAY_TOKEN`(onboard 时生成的 shared-secret,`Authorization: Bearer`) |
| `model` | `openclaw/default` | 代理别名(`openclaw/<agentId>` 可指定具体代理) |
| `timeout` | `120` | CUA 请求超时(秒) |
| `autostart` | `true` | 网关离线时自动 `Popen("openclaw gateway --port <port>")` 拉起(同 Hermes 惯例,日志落 `data/openclaw_gateway.log`,桌宠退出不杀) |

设置界面:设置 → Agent 模式 → 「OpenClaw 网关(本地代理)」下拉项 + OPENCLAW GATEWAY 配置块(含「检测 OpenClaw 网关」探活按钮,探测 `GET /v1/models`)。

## 部署前提(用户侧一次性)

```bash
npm install -g openclaw   # Node ≥ 22.22.3
openclaw onboard          # 生成 Gateway token 等配置
# CUA 路径还需安装 CUA skill(如 TuriX-CUA)
```

Gateway 可由 amadeus-py 自动拉起,也可手动常驻:`openclaw gateway`(或 `openclaw gateway install` 注册系统服务)。

## 相关文件

- `core/openclaw_client.py` — 配置合并 / 探活 / 自动拉起 / 对话后端 / CUA 委托
- `core/backend_router.py` — `"openclaw"` 路由分支(降级链与 hermes 同构)
- `core/desktop_tools.py` — `operate_gui` 薄封装(`on_status` 进度透传)
- `ui/settings_dialog.py` — OpenClaw 设置块
- 测试:`tests/test_openclaw_client.py`、`tests/test_openclaw_tool.py`、`tests/test_backend_router.py`
