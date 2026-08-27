# Amadeus · 红莉栖桌宠

> Kurisu — a Pure-Python Live2D desktop pet with a native transparent overlay.

一款 Windows 桌面 AI 桌宠：Live2D 红莉栖（牧瀬红莉栖）以透明无边框窗口悬浮在桌面上，支持语音对话、实时通话、终端工具、长期记忆与多模型路由。

## 功能特性

- **Live2D 桌宠**：PixiJS + Cubism 4 渲染，面部/身体跟随鼠标，情感动作系统（10 种情绪 × 歪头/叉腰/扶额/摊手等动作），闲置微动作（长时间无交互随机托腮/发呆）；WebView 只输出透明人物帧，窗口 UI 由 Qt 绘制。
- **语音对话**：ASR 实时识别（mimo-audio-v1 线路）+ TTS 多引擎（阿里云 / GPT-SoVITS / SAPI 自动降级）+ 音量驱动口型同步。
- **实时通话**：双向语音通话视图，支持挂断、屏幕共享给 AI 看。
- **聊天屏幕感知**：普通对话可选附加当前屏幕描述（视觉模型一句话总结，默认关）。
- **终端系统**：`/` 斜杠命令工具系统（类 Codex CLI），Markdown 渲染，CRT 效果，`Ctrl+=` / `Ctrl+-` 缩放。
- **Skills 系统**：可加载/读取本地技能包。
- **长期记忆**：类 Hermes 长期记忆系统 + 语义检索（OpenAI 兼容 /embeddings 向量召回，失败自动降级关键词匹配）。
- **Dock 栏**：macOS 风格 Dock，hover 邻近放大。
- **多模型路由**：DeepSeek / Ollama / Hermes / Harness 后端自动路由。
- **每周陪伴周报**：companion 调度器（评估 + 表达式 + 传感器）。

## 技术栈

| 层 | 技术 |
|----|------|
| UI 框架 | PySide6 (Qt) |
| Web 渲染 | pywebview 6.x（winforms + Edge WebView2 / Chromium） |
| Live2D | PixiJS `pixi-live2d-display` + Cubism 4 Core |
| Companion Canvas UI | PySide6（WebView 仅负责透明 Live2D Canvas2） |
| HTTP | 内置 ThreadingHTTPServer（renderer 静态资源） |
| 打包 | PyInstaller（onedir） |

## 快速开始

### 开发模式

```powershell
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key（不含密钥文件不入库，需自行创建）
#    data/config.json —— 见下方「配置」

# 3. 运行
python main.py
```

> GPT-SoVITS 为本地引擎，依赖外部服务 `http://127.0.0.1:9880`（不打入 exe），未启动时自动降级到 SAPI。

### 打包 exe

```powershell
D:\anaconda\python.exe -m PyInstaller Amadeus.spec --noconfirm
```

产物：`dist/Amadeus-<版本号>/Amadeus-<版本号>.exe`（目录发布，无控制台）。请压缩发布整个目录，详见 [docs/build.md](docs/build.md)。

## 配置

运行时配置存储在 `data/config.json`（**不入库**，含 API Key）：

```jsonc
{
  "llm": {
    "provider": "deepseek | ollama | hermes | harness",
    "api_key": "...",          // 或使用环境变量 DEEPSEEK_API_KEY
    "model": "deepseek-chat"
  },
  "tts": {
    "engine": "aliyun | gpt_sovits | sapi",
    "aliyun": { "api_key": "...", "voice_id": "..." }
  },
  "asr": { "model": "mimo-audio-v1", "endpoint": "...", "api_key": "..." }
}
```

## 项目结构

```
amadeus-py/
├── main.py                  # 入口：拉起桌宠子进程 + 系统托盘
├── desktop_pet.py           # 桌宠主进程：run_overlay + PetWindow 主窗口
├── config.py                # 角色配置
├── Amadeus.spec             # PyInstaller 打包配置
├── core/                    # 核心模块
│   ├── llm/                 #   模型后端与路由（DeepSeek/Ollama/Hermes/Harness/Codex/OpenClaw）
│   ├── voice/               #   语音全链路（TTS/ASR/VAD/AEC/GPT-SoVITS 进程/通话）
│   ├── vision/              #   屏幕感知与视觉（截屏/屏幕描述/视觉模型）
│   ├── companion/           #   每周陪伴调度器
│   ├── im/                  #   OneBot 消息接入
│   └── …                    #   会话/记忆/技能/终端/存储等基础设施
├── ui/                      # Qt 控件与非 Qt 纯函数助手
│   ├── bubble.py            #   气泡分段 / 流式决策纯函数
│   ├── terminal_html.py     #   终端 HTML 构建纯函数
│   ├── theme.py             #   fauux 抖动纹理
│   ├── renderer_proc.py     #   renderer 子进程（webview + Live2D 帧回传）
│   ├── settings_dialog.py   #   设置页
│   └── widgets/             #   Dock / 状态栏 / AgentTerminal / AgentTask…
├── live2d/                  # Live2D 页面与渲染（透明终端画布 + PIXI）
├── resources/               # 图标 / 纹理 / 模型 / 字体
├── scripts/                 # 辅助脚本
├── docs/                    # 文档（prd/ 需求稿 · archive/ 历史设计稿与退役资源）
└── tests/                   # 测试（按 llm/voice/vision/ui/core/companion 分组）
```

## 常见问题

- **exe 启动慢？** 请从发布目录启动 exe；onedir 不需要启动时解压大型归档。
- **exe 打不开？** 查看 exe 同级 `data/logs/startup-crash.log` 或 `data/logs/renderer-crash.log`；若提示 WebView，安装 Microsoft Edge WebView2 Runtime。
- **语音没有声音？** 检查 `data/config.json` 的 TTS 引擎配置；GPT-SoVITS 未启动时会降级 SAPI。
