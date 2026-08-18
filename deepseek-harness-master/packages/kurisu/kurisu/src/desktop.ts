/**
 * 桌面工具：截图 / 剪贴板读写 / 窗口枚举，作为 model 可调用的工具注册到 ctx.tools。
 * 底层通过 Windows PowerShell 实现，无需 native 依赖；非 Windows 平台会失败并返回错误文本。
 */
import { execFile } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { promisify } from 'node:util'
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'

const execFileAsync = promisify(execFile)

function psQuote(s: string): string {
  return `'${s.replace(/'/g, "''")}'`
}

async function runPs(script: string): Promise<string> {
  const { stdout } = await execFileAsync('powershell', ['-NoProfile', '-NonInteractive', '-Command', script], {
    windowsHide: true,
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
  })
  return stdout.toString().trim()
}

/** 字符串输出的工具渲染 */
function textRender(_args: unknown, value: string): { type: 'text'; text: string }[] {
  return [{ type: 'text', text: value }]
}

export function installDesktopTools(ctx: Context): void {
  ctx.tools.register(defineTool({
    name: 'kurisu_screenshot',
    description: '截取主屏幕并保存为 PNG 文件，返回绝对路径',
    parameters: {
      path: { type: 'string', description: '保存路径，留空存到系统临时目录' },
    },
    output: { schema: { type: 'string' }, render: textRender },
    async execute(args) {
      const path = args.path ?? join(tmpdir(), `kurisu-shot-${Date.now()}.png`)
      const script = `Add-Type -AssemblyName System.Windows.Forms,System.Drawing; `
        + `$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds; `
        + `$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; `
        + `$g=[System.Drawing.Graphics]::FromImage($bmp); `
        + `$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size); `
        + `$bmp.Save(${psQuote(path)},[System.Drawing.Imaging.ImageFormat]::Png); `
        + `$g.Dispose(); $bmp.Dispose()`
      await runPs(script)
      return path
    },
  }))

  ctx.tools.register(defineTool({
    name: 'kurisu_clipboard_read',
    description: '读取系统剪贴板文本内容',
    parameters: {},
    output: { schema: { type: 'string' }, render: textRender },
    async execute() {
      return runPs('Get-Clipboard -Raw')
    },
  }))

  ctx.tools.register(defineTool({
    name: 'kurisu_clipboard_write',
    description: '把文本写入系统剪贴板',
    parameters: {
      text: { type: 'string', required: true, description: '要写入剪贴板的文本' },
    },
    output: { schema: { type: 'string' }, render: textRender },
    async execute(args) {
      await runPs(`Set-Clipboard -Value ${psQuote(args.text)}`)
      return 'ok'
    },
  }))

  ctx.tools.register(defineTool({
    name: 'kurisu_list_windows',
    description: '枚举当前有标题的窗口，返回 JSON 数组（进程 id / 进程名 / 标题）',
    parameters: {},
    output: { schema: { type: 'string' }, render: textRender },
    async execute() {
      const script = `Get-Process | Where-Object { $_.MainWindowTitle } `
        + `| Select-Object Id,ProcessName,MainWindowTitle | ConvertTo-Json -Compress`
      return runPs(script)
    },
  }))
}
