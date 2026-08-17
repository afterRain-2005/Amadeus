#!/usr/bin/env node
// 物化 pnpm deploy 生成的 node 闭包，使其可运行且无符号链接。
//
// 背景：Windows 上 `pnpm deploy --legacy` 会把 workspace 包（@deepseek-ai/* 等）
// 拷贝成“浅目录”（只有 package.json + README），漏掉编译产物 lib/。入口
// runtime/node/node_modules/@deepseek-ai/dsh-sdk-jsonrpc-demo/lib/packaged-bin.js
// 因此缺失。本脚本从 monorepo 源码目录把这些包（含 lib/）整包拷进闭包，
// 并补齐被 legacy deploy 提升到 python/sdk-runtime/node_modules 的第三方依赖，
// 最后移除 .bin 垫片目录（含符号链接，会破坏 PyInstaller）。
//
// 对应官方 scripts/build-exe-for-python-sdk.ts 的 restoreLegacyHoists() 与
// materializeStagedLinks() 在 Windows 上的等价实现。
import { existsSync } from 'node:fs'
import { cp, lstat, readdir, readFile, rm } from 'node:fs/promises'
import { dirname, join, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
// 脚本位于 <project>/scripts/，harness 仓库在 <project>/deepseek-harness-master/。
const root = process.argv[2] ?? resolve(here, '..', 'deepseek-harness-master')
const STAGING = join(root, 'python', 'sdk-runtime', 'src', 'deepseek_harness_runtime', 'runtime', 'node')
const SOURCE_NODE_MODULES = join(root, 'python', 'sdk-runtime', 'node_modules')
// pnpm-workspace.yaml 中除部署根（python/sdk-runtime）以外的 workspace 顶层目录。
const WORKSPACE_ROOTS = ['vendor', 'packages', 'native', 'apps', 'website', 'examples']

async function findPackageJsonFiles(dir, out = []) {
  let entries
  try {
    entries = await readdir(dir, { withFileTypes: true })
  } catch {
    return out
  }
  for (const entry of entries) {
    if (['node_modules', '.pnpm-store', 'dist-exe', '.git'].includes(entry.name)) continue
    const path = join(dir, entry.name)
    if (entry.isDirectory()) {
      await findPackageJsonFiles(path, out)
    } else if (entry.name === 'package.json') {
      out.push(path)
    }
  }
  return out
}

async function collectWorkspaceMap() {
  const map = new Map()
  for (const wsRoot of WORKSPACE_ROOTS) {
    const abs = join(root, wsRoot)
    if (!existsSync(abs)) continue
    for (const manifestPath of await findPackageJsonFiles(abs)) {
      try {
        const pkg = JSON.parse(await readFile(manifestPath, 'utf8'))
        if (pkg.name) map.set(pkg.name, dirname(manifestPath))
      } catch {
        // 跳过解析失败的 package.json
      }
    }
  }
  return map
}

function excludeNestedNodeModules(source) {
  const nested = join(source, 'node_modules')
  return path => path !== nested && !path.startsWith(nested + sep)
}

async function copyTree(source, destination) {
  // 先移除目标，避免目录链接被递归删除时跟随到真实内容。
  try {
    const stat = await lstat(destination)
    if (stat.isSymbolicLink()) {
      await rm(destination, { force: true })
    } else {
      await rm(destination, { recursive: true, force: true })
    }
  } catch {
    // 目标不存在则忽略
  }
  await cp(source, destination, {
    recursive: true,
    dereference: true,
    force: true,
    filter: excludeNestedNodeModules(source),
  })
}

async function main() {
  const manifestPath = join(STAGING, 'package.json')
  if (!existsSync(manifestPath)) {
    throw new Error(`staging manifest 缺失：${manifestPath}`)
  }
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
  const deps = Object.keys(manifest.dependencies ?? {})
  const workspaceMap = await collectWorkspaceMap()

  const copied = []
  const restored = []
  const missing = []

  for (const name of deps.sort()) {
    const destination = join(STAGING, 'node_modules', name)
    const source = workspaceMap.get(name)
    if (source && existsSync(source)) {
      await copyTree(source, destination)
      copied.push(name)
      continue
    }
    if (existsSync(destination)) continue // 第三方依赖已由 deploy 就位
    const hoisted = join(SOURCE_NODE_MODULES, name)
    if (existsSync(hoisted)) {
      await copyTree(hoisted, destination)
      restored.push(name)
      continue
    }
    missing.push(name)
  }

  // 移除 .bin 垫片目录（含符号链接，会破坏 PyInstaller 打包）。
  const nodeModules = join(STAGING, 'node_modules')
  const binDir = join(nodeModules, '.bin')
  if (existsSync(binDir)) {
    await rm(binDir, { recursive: true, force: true })
    console.log('removed .bin shim directory')
  }

  if (copied.length > 0) {
    console.log(`materialized workspace packages (${copied.length}): ${copied.join(', ')}`)
  }
  if (restored.length > 0) {
    console.log(`restored hoisted deps (${restored.length}): ${restored.join(', ')}`)
  }
  if (missing.length > 0) {
    throw new Error(`依赖在闭包与提升源中都缺失：${missing.join(', ')}`)
  }

  const entry = join(nodeModules, '@deepseek-ai', 'dsh-sdk-jsonrpc-demo', 'lib', 'packaged-bin.js')
  if (!existsSync(entry)) {
    throw new Error(`物化后入口仍缺失：${entry}`)
  }
  console.log(`closure materialization OK; entry present: ${entry}`)
}

await main()
