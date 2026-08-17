# 构建 DeepSeek Harness TypeScript 项目 + 部署 node 闭包
# 在 amadeus-py 根目录运行：.\scripts\build_harness.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$harnessDir = Join-Path $root "deepseek-harness-master"
$runtimeNodeDir = "python/sdk-runtime/src/deepseek_harness_runtime/runtime/node"

Write-Host "=== 构建 DeepSeek Harness ===" -ForegroundColor Cyan

# 1. 检查 Node.js（node 闭包需要 >= 22.19）
$nodeVer = node --version 2>$null
if (-not $nodeVer) {
    Write-Error "需要 Node.js >= 22.19，请先安装 https://nodejs.org/"
    exit 1
}
Write-Host "Node.js: $nodeVer" -ForegroundColor Green

Push-Location $harnessDir
try {
    # 2. 安装依赖
    Write-Host "安装依赖（首次较慢，约 1-3 分钟）..." -ForegroundColor Yellow
    npx pnpm install --no-optional --ignore-scripts
    Write-Host "依赖安装完成" -ForegroundColor Green

    # 3. 构建所有包
    Write-Host "构建项目..." -ForegroundColor Yellow
    npx pnpm run build
    Write-Host "构建完成！" -ForegroundColor Green

    # 4. 部署 node 闭包到 SDK runtime 目录（Windows 只需这一步，不构建 exe）
    Write-Host "部署 node 闭包..." -ForegroundColor Yellow
    $staging = Join-Path $harnessDir $runtimeNodeDir
    if (Test-Path $staging) {
        Remove-Item $staging -Recurse -Force
    }
    npx pnpm --filter dsh-jsonrpc-agent-pkg deploy --legacy --prod `
        --config.node-linker=hoisted `
        --config.auto-install-peers=false `
        --config.link-workspace-packages=true `
        $runtimeNodeDir
    Write-Host "node 闭包部署完成！" -ForegroundColor Green

    # 5. 物化闭包：Windows 上 `pnpm deploy --legacy` 会把 workspace 包拷成浅目录
    #    （只有 package.json + README，漏掉编译产物 lib/），导致入口 packaged-bin.js
    #    缺失。用 Node 脚本从 monorepo 源码补全这些包、补齐被提升的第三方依赖、
    #    并移除 .bin 垫片目录。
    Write-Host "物化 node 闭包（补全 workspace 编译产物）..." -ForegroundColor Yellow
    $scriptRoot = Split-Path -Parent $PSCommandPath
    node (Join-Path $scriptRoot "materialize_closure.mjs") $harnessDir
    Write-Host "node 闭包物化完成！" -ForegroundColor Green
} finally {
    Pop-Location
}

Write-Host "=== DeepSeek Harness 构建完成 ===" -ForegroundColor Cyan
Write-Host "现在可以在设置页面选择 'DeepSeek Harness SDK' 模式（需系统 Node >= 22.19）" -ForegroundColor Yellow