# Amadeus 打包指南

本文档教你如何把 `amadeus-py` 打包成 Windows 可发布目录（包含 exe）。

---

## 1. 前置环境

| 项目 | 要求 | 本项目实际值 |
|------|------|--------------|
| 操作系统 | Windows 10/11 | Windows 11 |
| Python | 3.11+ | 3.13.9（conda） |
| 打包工具 | PyInstaller | 6.21.0 |
| 打包解释器 | 需装齐依赖的 Python | `D:\anaconda\python.exe` |

> **重要**：本项目用 anaconda base 环境打包，而不是项目里的 `.venv`。
> `.venv` 只有 PySide6，缺少 miniaudio 等运行时依赖；anaconda base 才是完整打包环境。

### 1.1 首次准备（已装过可跳过）

```powershell
# 安装打包工具
D:\anaconda\python.exe -m pip install pyinstaller

# 安装运行时依赖（requirements.txt 里已列出，但 miniaudio 需单独确认）
D:\anaconda\python.exe -m pip install miniaudio markdown
```

---

## 2. 修改版本号

版本号定义在 [core/version.py](../core/version.py)：

```python
__version__ = "0.4.1"
```

打包产物名会自动带上版本号，例如 `Amadeus-0.4.1.exe`。
**每次发布新版前，先改这个值**，否则会覆盖旧版 exe。

---

## 3. 打包

在项目根目录（`amadeus-py/`）执行：

```powershell
D:\anaconda\python.exe -m PyInstaller Amadeus.spec --noconfirm
```

- `--noconfirm`：不询问直接覆盖旧的 `build/` 中间产物
- 全程约 6 分钟
- 使用 onedir 发布，不需要启动时解压大型单文件归档，避免被 Windows 安全软件拦截或长时间卡在 bootloader。

---

## 4. 产物

打包完成后，exe 位于：

```
dist/Amadeus-<版本号>/Amadeus-<版本号>.exe
```

请把整个 `Amadeus-<版本号>/` 目录压缩发布，不要只复制其中的 exe；目录内包含 Qt、Live2D 和 Python 运行时文件。

---

## 5. 验证

运行 exe 后检查：

1. 桌宠窗口直接出现（无登录窗）
2. 设置页「关于」显示的版本号与 `core/version.py` 一致
3. 阿里云 TTS 能正常出声（需 `data/config.json` 里有正确的 API key 与 engine）
4. 终端窗口（dock 三条杠）能打开、能 markdown 渲染、`Ctrl+=` / `Ctrl+-` 缩放、CRT 闪烁

> GPT-SoVITS 是本地引擎，依赖外部服务 `http://127.0.0.1:9880`，**不打入 exe**。
> 未启动该服务时自动降级到 SAPI。

---

### 5.1 DeepSeek Harness 集成

项目已集成 DeepSeek Harness 的 Python SDK，提供了完整的 agent 能力（工具调用、审批、会话管理等）。

- **设置页面**：可切换 Agent 模式为 `DeepSeek Harness SDK`，配置 Provider（Harness 的 base_url/api_key 为空时复用全局 endpoint/api_key）
- **回退机制**：如果 Harness SDK 运行时不可用，自动回退到 `DeepSeek 直连` 模式（`core/deepseek_client.py`）
- **Windows 平台约束**：生产单文件 exe 是 documented non-goal（仅 linux/macos），Windows 上只能使用 dev-only 的 **node 闭包**，因此**运行环境必须安装 Node >= 22.19**（打包后的 exe 也依赖系统 Node）。
- **构建 Harness**（首次使用前，一次性）：

  ```powershell
  cd deepseek-harness-master

  # 1. 安装依赖（首次约 1-3 分钟，网络慢可能超时，可加 --registry https://registry.npmmirror.com）
  npx pnpm install --no-optional --ignore-scripts

  # 2. 构建所有包（产物 lib/）
  npx pnpm run build

  # 3. 部署 node 闭包到 SDK runtime 目录（Windows 只需这一步，不需要构建 exe）
  npx pnpm --filter dsh-jsonrpc-agent-pkg deploy --legacy --prod --config.node-linker=hoisted --config.auto-install-peers=false --config.link-workspace-packages=true python/sdk-runtime/src/deepseek_harness_runtime/runtime/node
  ```

  产物：`python/sdk-runtime/src/deepseek_harness_runtime/runtime/node/node_modules/@deepseek-ai/dsh-sdk-jsonrpc-demo/lib/packaged-bin.js`。打包时整个 `deepseek_harness_runtime/` 目录会作为 `datas` 打进 exe。

- **架构说明**：
  ```
  终端 UI → route_and_send(harness) → harness_bridge.run_harness_turn
            → DeepSeekHarness SDK → JSON-RPC over stdio → Node.js 运行时子进程
  ```
  Windows 上通过 `launch_args_override` 显式指定 node 闭包启动，并注入默认 `cordis.yml`。

---

## 6. 常见问题排查

### 6.1 运行时 `ModuleNotFoundError: No module named 'miniaudio'`
**原因**：`core/mp3_decoder.py` 在函数内动态 `import miniaudio`，PyInstaller 静态分析抓不到。
**修复**：确认 [Amadeus.spec](../Amadeus.spec) 的 `hiddenimports` 里有 `'miniaudio'`。

### 6.2 终端 markdown 不渲染 / 无代码块
**原因**：markdown 的 extensions（`fenced_code` / `tables` / `nl2br`）也是动态 import。
**修复**：确认 `hiddenimports` 里包含：
```python
'markdown', 'markdown.extensions.fenced_code',
'markdown.extensions.tables', 'markdown.extensions.nl2br',
```

### 6.3 打包时大量 `Library not found: could not resolve ... DLL`
例子：`ntdll.dll`、`bcrypt.dll`、`Secur32.dll` 等。
**结论**：**正常，可忽略**。这些是 Windows 系统 DLL，运行时由操作系统提供，不需要打入 exe。

### 6.4 `Hidden import "pywebview.platforms.edgechromium" not found`
**结论**：正常。pywebview 在运行时按平台动态选择后端，此 warning 不影响。

### 6.5 anaconda 打包体积异常大 / 混入 Qt
**原因**：anaconda base 同时装了 PyQt5/PyQt6，会与 PySide6 冲突。
**修复**：确认 [Amadeus.spec](../Amadeus.spec) 的 `excludes` 里已排除：
```python
'PyQt5', 'PyQt6', 'qtpy',
'matplotlib', 'scipy', 'pandas', 'botocore', 'boto3', 'IPython',
```

### 6.6 exe 启动后无窗口
运行数据写在 exe 同级的 `data/`，不会写入 PyInstaller 的临时解压目录。
启动诊断日志位于 `data/logs/`：`startup-crash.log`、`desktop-pet-crash.log`、
`renderer-crash.log`。Live2D 渲染需要系统安装 Microsoft Edge WebView2 Runtime。

---

## 7. spec 文件要点（进阶）

[Amadeus.spec](../Amadeus.spec) 是打包的单一事实来源，关键配置：

| 配置项 | 作用 |
|--------|------|
| `Analysis(['main.py'])` | 入口脚本，从它出发分析 import 依赖 |
| `datas` | 打包 `resources/`（Live2D 模型/图标）和 `live2d/`（网页+运行时） |
| `hiddenimports` | 补上动态 import 漏掉的库（miniaudio、markdown extensions） |
| `excludes` | 排除 anaconda 科学栈和多余 Qt 绑定，瘦身 |
| `name=f'Amadeus-{__version__}'` | 产物名自动带版本号 |
| `console=False` | 无控制台黑窗 |
| `COLLECT(...)` | 生成稳定的 onedir 发布目录，避免 onefile 启动解压卡顿 |

新增「运行时动态 import」的第三方库时，**必须在 `hiddenimports` 里手动加上**，否则 exe 运行时找不到该模块。

---

## 8. 一句话速查

```powershell
# 1. 改版本号：core/version.py 的 __version__
# 2. 打包：
D:\anaconda\python.exe -m PyInstaller Amadeus.spec --noconfirm
# 3. 产物：dist/Amadeus-<版本号>/Amadeus-<版本号>.exe
```
