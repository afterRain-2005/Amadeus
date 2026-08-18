/**
 * /kurisu 命令：不经过模型，直接返回助手状态与问候。
 */
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-commands'
import type { KurisuConfig } from './settings.ts'

export function installCommands(ctx: Context, getConfig: () => KurisuConfig): void {
  ctx.commands.register({
    name: 'kurisu',
    description: '查看 Kurisu 陪伴助手状态并打招呼',
    handler: () => {
      const c = getConfig()
      return {
        kind: 'success',
        text: `你好，我是${c.personaName}。`
          + `语音：${c.ttsEnabled ? c.ttsEngine : '关闭'}`
          + `，主动陪伴：${c.companionEnabled ? '开启' : '关闭'}`
          + `，桌面工具：${c.desktopToolsEnabled ? '开启' : '关闭'}`,
      }
    },
  })
}
