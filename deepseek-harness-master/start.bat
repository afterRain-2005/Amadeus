@echo off
setlocal
cd /d "%~dp0"

set "NODE_OPTIONS=--max_old_space_size=4096"

if not exist "node_modules" (
  echo [start] node_modules missing, installing dependencies...
  corepack pnpm install --frozen-lockfile --ignore-scripts
  if errorlevel 1 exit /b 1
)

echo [start] launching deepseek-harness web server on http://127.0.0.1:3080
node apps/cli/lib/bin.js web
