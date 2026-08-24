# Amadeus 原生构建与发布指南

当前发布链路是 Rust + Tauri 2，生成 Windows MSI 与 NSIS 安装包。最终用户无需
Python、Node.js 或 Rust；WebView2 缺失时由安装器的官方 bootstrapper 静默安装。

## 构建环境

| 项目 | 要求 |
|---|---|
| 系统 | Windows 10/11 x64 |
| Rust | 1.88 或更高，MSVC toolchain |
| Node.js | 22 或更高 |
| pnpm | 11.x（可通过 Corepack 启用） |
| WebView2 | 开发机需安装；安装包已配置 bootstrapper |

首次准备：

```powershell
corepack enable
pnpm --dir apps/desktop-tauri install --frozen-lockfile
```

## 开发与本地启动

```powershell
start.bat --dev
```

已有二进制时，`start.bat` 无参数会按“已安装版本 → 本地 release → 本地 debug”
的顺序启动。`start.bat --console` 用于保留当前控制台，`start.bat --help` 显示帮助。

## 发布构建

版本号需同步保持一致：

- 根目录 `Cargo.toml` 的 `workspace.package.version`
- `apps/desktop-tauri/package.json`
- `apps/desktop-tauri/src-tauri/tauri.conf.json`

执行：

```powershell
start.bat --build
```

或直接执行：

```powershell
pnpm --dir apps/desktop-tauri tauri build
```

典型产物：

```text
target/release/amadeus-desktop.exe
target/release/bundle/msi/Amadeus Next_0.10.0_x64_zh-CN.msi
target/release/bundle/nsis/Amadeus Next_0.10.0_x64-setup.exe
```

## 发布前自动检查

```powershell
cargo fmt --all -- --check
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
pnpm --dir apps/desktop-tauri build
pnpm --dir apps/desktop-tauri audit --registry https://registry.npmjs.org
cargo audit --target-os windows --target-arch x86_64
git diff --check
```

Rust 审计必须没有 Windows 目标漏洞。由 Tauri 的 Linux/GTK 可选目标传递而来的
warning 应单独记录，不能误写为 Windows 安装包漏洞。

## 安装包验收

每个候选版本至少验证：

1. NSIS 和 MSI 都能安装、从开始菜单启动、升级并卸载。
2. 未安装开发工具的干净 Windows 10/11 x64 虚拟机可以启动。
3. `280×560` Live2D 手机窗口无白框，标题拖动区可拖动；独立设置窗口九个页面、
   独立终端和 WIRED Rose/Aqua 实时切换均可访问。
4. 第二次启动只恢复现有窗口；关闭按钮隐藏到托盘，托盘“退出”真正结束进程。
5. 重启后历史、记忆和非敏感设置仍在；凭据只存在 Credential Manager。
6. 麦克风挂断、屏幕共享关闭、Agent 取消和应用退出均释放其资源。
7. 无云 TTS 凭据时 SAPI 可完成发声；设备拔插/停顿进入自动恢复而非结束通话。
8. OneBot 关闭或断线、传感器失败、版本检查失败均不阻断直连文字聊天。

本地构建的未签名安装包只能用于测试。公开发布前必须使用发布者代码签名证书签名
MSI、NSIS 和主 EXE，并在干净虚拟机重跑上述验收；不得把未签名产物描述为已签名。

## 运行数据和兼容性

应用数据位于 Tauri 的 `com.wweiyi.amadeus.next` 用户配置目录。会话和记忆使用
SQLite WAL；模型、ASR、TTS、OneBot 密钥使用 Windows Credential Manager。
启动时会尝试导入旧会话/记忆数据，但旧 Python 运行时、PyInstaller、Qt、Harness
和 GPT-SoVITS 启动器都不是新安装包依赖。
