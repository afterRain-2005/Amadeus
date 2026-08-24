# Amadeus · 红莉栖桌宠

Amadeus 是面向 Windows 10/11 的原生 AI 桌宠。当前默认实现已迁移到
Rust + Tauri：透明 Live2D 窗口、文字与语音对话、长期记忆、Codex Agent、
Companion 感知和 QQ 通知均在同一个可安装桌面应用中闭环，不需要 Python。

## 已完成功能

- 无白框 `280×560` Live2D 手机桌宠，可拖动、置顶、托盘隐藏/退出，限制单实例。
- 旧版 WIRED Rose 视觉、可选 Aqua 主题，以及独立设置窗口和独立 Agent 终端。
- OpenAI 兼容的流式文字对话，可取消，支持多会话历史和本地长期记忆。
- 原生音频设备选择、VAD、ASR、流式分句 TTS、音量口型、AEC 和打断续说。
- 通话中显式开启屏幕共享；截图只在内存中压缩并随当前轮次发送。
- 直连模式与 Codex Agent 模式；Codex 子进程受 Windows Job Object 管理，
  只提供 `read-only` 和用户显式选择的 `workspace-write` 沙箱。
- Agent 终端含命令历史、状态、路由切换、取消和工具时间线。
- 前台应用/空闲时间感知、可选剪贴板感知、安静时段、冷却和每日次数限制。
- OneBot QQ 反向 WebSocket 通知、群聊过滤、去重、七天记录和系统通知。
- 启动/手动版本检查，只打开官方发布页，不自动下载或执行更新程序。

## 普通用户启动

优先从 Release 下载 MSI 或 NSIS 安装包。安装后可从开始菜单启动；仓库中的
`start.bat` 会依次寻找已安装版本、本地 release、debug，均不存在时才进入开发模式。

首次打开后，在设置中完成：

1. “模型”页填写 OpenAI 兼容 Endpoint、模型名和 API Key。
2. “音频”页选择输入/输出设备及 ASR；TTS 未配置云音色时可使用 Windows SAPI。
3. 如需 Agent，在“Agent”页选择 Codex、工作区和沙箱级别。Codex CLI 需由用户
   单独安装并登录；直连聊天不依赖 Codex。

API Key 和 OneBot token 保存在 Windows Credential Manager，JSON 配置不含密钥。
屏幕和剪贴板访问默认关闭。

## 开发与构建

要求：Windows 10/11、Rust 1.88+、Node.js 22+、pnpm 11+。

```powershell
corepack enable
pnpm --dir apps/desktop-tauri install --frozen-lockfile
start.bat --dev
```

构建 MSI 和 NSIS：

```powershell
start.bat --build
```

产物位于 `target/release/bundle/`。完整环境、验收和发布步骤见
[打包指南](docs/build.md)，迁移范围见[迁移矩阵](docs/migration-status.md)。

## 质量检查

```powershell
cargo fmt --all -- --check
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
pnpm --dir apps/desktop-tauri build
```

## 项目结构

```text
apps/desktop-tauri/          Tauri/WebView2 UI 与 Rust 桌面运行时
crates/amadeus-core/         类型化协议和受监管子进程生命周期
live2d/ + resources/         随安装包发布的 Live2D 与前端资源
docs/                        架构、迁移矩阵和发布说明
scripts/                     资源提取及诊断工具
main.py/desktop_pet.py        旧 Python 启动入口，仅作迁移溯源
core/ + ui/ + tests/         v0.9.2 重组后的旧 Python 实现与测试
```

Python/PyInstaller 路线不再是默认启动、构建或发布路径。
