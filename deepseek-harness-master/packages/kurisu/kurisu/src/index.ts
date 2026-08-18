/**
 * Kurisu 陪伴插件主入口：一个强大的 harness plugin，聚合
 * 人设、TTS 语音、桌面工具、主动陪伴、设置与命令。
 */
import type { Context } from '@deepseek-ai/cordis'
import { KURISU_PERSONA } from './persona.ts'
import { installCommands } from './commands.ts'
import { installCompanion } from './companion.ts'
import { installDesktopTools } from './desktop.ts'
import { KurisuConfigSchema, KURISU_NS } from './settings.ts'
import type { KurisuConfig } from './settings.ts'
import { installTts } from './tts.ts'

export const name = 'kurisu'
export const inject = ['llm', 'tools', 'systemPrompt', 'settings', 'commands', 'agents']

export function apply(ctx: Context): void {
  const scope = ctx.settings.register(KURISU_NS, KurisuConfigSchema)
  const getConfig = (): KurisuConfig => scope.get()

  // 人设 system prompt（order 0 = persona 惯例层级）
  ctx.systemPrompt.section({
    name: 'kurisu-persona',
    order: 0,
    text: KURISU_PERSONA,
  })

  installTts(ctx, getConfig)
  installCommands(ctx, getConfig)
  installCompanion(ctx, getConfig)
  if (getConfig().desktopToolsEnabled) installDesktopTools(ctx)
}
