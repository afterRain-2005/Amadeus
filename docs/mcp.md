# MCP 配置

项目级 MCP 配置位于 `.zcode/config.json`。首次启动某个服务时，`npx` 或 `uvx` 会自动下载对应运行时，因此需要 Node.js、`uvx` 和网络访问。

当前启用的服务：

- `context7`：查询常用库的最新文档。
- `filesystem`：访问项目目录内的文件。
- `git`：查看提交、差异和仓库状态。
- `fetch`：抓取公开 HTTP/HTTPS 页面。
- `playwright`：浏览器自动化和页面检查。
- `sequential-thinking`：复杂任务的分步推理。
- `sqlite-memory`：访问项目的 `data/memory.db`。

`filesystem` 和 `git` 已限制到当前项目目录；`fetch` 只用于公开网页。配置没有写入 API Key，也没有启用需要个人凭据的 GitHub MCP。

## Windows 使用

1. 确认 `node`、`npx` 和 `uvx` 已加入 `PATH`。
2. 重启使用 `.zcode/config.json` 的客户端，让它重新加载 MCP 配置。
3. 首次调用服务时允许依赖下载；如果网络受限，可先手动执行对应命令检查环境。

如果改为直接使用 Codex CLI，需要在用户级 Codex 配置中单独注册服务；`.zcode/config.json` 不会自动写入 `~/.codex/config.toml`。
