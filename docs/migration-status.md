# Amadeus legacy-to-Tauri migration matrix

本文件是迁移范围和发布验收的单一事实来源。“Complete”表示用户路径已在 Tauri UI
闭环、需要持久化的状态可跨重启、关键逻辑有自动测试并进入 MSI/NSIS 构建。

## 范围决策

| 旧功能/设想 | 决策 | 原因 |
|---|---|---|
| 多角色与登录 | 不迁移 | 产品确定为 Kurisu 单角色、无账号登录。 |
| 文件轮询 IPC、通用 `run_command` | 不迁移 | 已由类型化 Tauri command/event 替代。 |
| 自动下载并执行更新 | 不迁移 | 只提示版本并打开固定官方发布页，缩小供应链风险。 |
| WeChat adapter | 不迁移 | 旧版没有可工作的实现。 |
| Hermes/OpenClaw/Harness 独立后端 | 替代完成 | 用户选择当前 Codex 架构；产品后端收敛为直连 OpenAI 兼容接口与 Codex。 |
| 参数级桌面工具审批桥 | 替代完成 | 不向 WebView/模型开放通用本机工具；Codex 使用只读或用户显式启用的工作区写入沙箱。 |
| Python/Qt/PyInstaller 默认运行时 | 不迁移 | 原生 Rust/Tauri 是默认启动、构建和发布路径。 |
| GPT-SoVITS 启动器、SSH 控制 | 不迁移 | 发布版使用阿里云 TTS 与可取消 SAPI fallback，不引入 Python/远程控制依赖。 |

## 用户功能矩阵

| Area | Tauri status | Completion evidence |
|---|---|---|
| Shell | Complete | 无白框 `280×560` 手机窗口、旧版状态栏/方形气泡/六图标 Dock、拖动区、置顶、托盘、单实例、隐藏/退出；严格 CSP 下实机渲染。 |
| Appearance | Complete | WIRED Rose/Aqua 双主题、原子持久化、三窗口实时同步；设置与 Agent 终端恢复为独立大窗口。 |
| Text chat | Complete | OpenAI 兼容 SSE、上下文、取消、错误隔离、HTTPS/loopback 校验。 |
| Model secrets | Replaced/complete | Windows Credential Manager；JSON 只写非敏感元数据且原子替换。 |
| Voice input | Complete | CPAL 设备选择、无锁采集、持续时间 VAD、ASR、静音/挂断、热插拔/停滞恢复。 |
| Voice output | Complete | 阿里云 TTS、可取消 SAPI fallback、流式分句、有界解码和实际 RMS 口型。 |
| Full-duplex call | Complete | Rust NLMS/NLP AEC、冷启动保守阈值、打断续说、轮次隔离取消。 |
| Conversation persistence | Complete | SQLite WAL，会话新建/切换/删除、历史 UI、旧 JSON 导入、并发写入压力测试。 |
| Long-term memory | Complete | facts/episodes 提取、本地召回、提示词注入、查看/编辑/删除/清空、旧 DB 导入。 |
| Screen-aware calls | Complete | 每次通话显式开启、持续可见指示、GDI 内存截图、缩放 JPEG、当前轮次附件、立即停止。 |
| Backend routing | Replaced/complete | 直连 + Codex；CLI 探测超时、精确 thread resume、stdin prompt、Job Object 进程树回收。 |
| Agent terminal | Complete | 独立 `720×520` 窗口；`/help`、`/clear`、`/new`、`/status`、`/route`、历史、取消、工具时间线。 |
| Companion sensing | Complete | 前台应用和空闲时间；剪贴板独立选择且默认关；快照 UI 和失败隔离。 |
| Proactive companion | Complete | 主开关、安静时段、冷却、每日上限、测试触发、可取消调度器。 |
| QQ notifications | Complete | OneBot 反向 WS/WSS、Bearer 凭据、2 MiB 上限、去重、过滤、七天 SQLite、气泡/系统通知。 |
| Version notice | Complete | 启动/手动检查、5 秒和 1 MiB 上限、数字版本比较、固定官方发布页，无自动执行。 |
| Release hardening | Complete (local) | 无 inline/eval CSP、统一取消、配置原子写、Rust/TS 测试与审计、release EXE、MSI/NSIS 构建及本机 smoke。 |

## 发布验收

- 凭据不出现在明文 JSON、日志、前端状态、URL、提示词或命令行参数中。
- 麦克风、截图、网络、播放、调度器和子进程均有 owner 与取消路径，旧数据不跨会话。
- 屏幕和剪贴板默认关闭，屏幕共享期间持续显示状态。
- 记忆、传感器、可选 IM、TTS 或版本检查失败不能阻止直连文字聊天。
- MSI/NSIS 的最终用户运行不依赖 Python、Node.js 或 Rust。
- 公开发布仍需发布者证书签名及干净 Windows 10/11 VM 验收；这是发布基础设施门槛，
  不是未迁移的应用功能，本地未签名产物不得标记为已签名。
