# Amadeus Tauri desktop

这是 Amadeus 当前默认桌面实现，版本 0.10.0。

```powershell
pnpm install --frozen-lockfile
pnpm tauri dev
pnpm tauri build
```

前端只负责表现和类型化 IPC；模型、凭据、SQLite、音频、截图、OneBot、
Codex 子进程与生命周期均由 Rust 端负责。发布说明见仓库根目录的
`docs/build.md`，功能范围以 `docs/migration-status.md` 为准。

窗口结构沿用旧版交互，并针对 Windows WebView2 收紧为无白框 `280×560` 手机主窗口；设置与 Agent 终端
分别使用 `760×560`、`720×520` 独立窗口。WIRED Rose/Aqua 主题由 Rust 端
原子持久化，并通过事件同步到全部窗口。
