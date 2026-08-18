/**
 * 主动陪伴：监听 session/event，在用户静默一段时间后向最近的 agent 注入一条陪伴消息，
 * 唤醒模型主动关心用户。空闲时长由 settings 控制，避免死循环（只在 turn/end 重置定时器）。
 */
import type { Context } from '@deepseek-ai/cordis'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import type { SessionEvent } from '@deepseek-ai/dsh-session'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type { KurisuConfig } from './settings.ts'

const COMPANION_PROMPTS = [
  '已经好一会儿没动静了，随便说点什么吧。',
  '你在忙吗？如果需要帮忙可以直接说。',
  '我还在哦，别一个人闷着。',
]

export function installCompanion(ctx: Context, getConfig: () => KurisuConfig): void {
  let lastAgent: Agent | undefined
  let timer: ReturnType<typeof setTimeout> | undefined

  function fire(): void {
    if (timer) clearTimeout(timer)
    timer = undefined
    const agent = lastAgent
    if (!agent || agent.status !== 'idle') return
    const prompt = COMPANION_PROMPTS[Math.floor(Math.random() * COMPANION_PROMPTS.length)] ?? COMPANION_PROMPTS[0]
    agent.followup(createUserMessage({
      content: [{ type: 'text', text: `[陪伴触发] ${prompt}（不要提及本条提醒）` }],
      source: { kind: 'user' },
    }))
  }

  function reset(): void {
    if (timer) clearTimeout(timer)
    if (!getConfig().companionEnabled) return
    timer = setTimeout(fire, getConfig().companionIdleMs)
  }

  ctx.on('session/event', (session, event: SessionEvent) => {
    if (event.type !== 'turn/end') return
    lastAgent = ctx.agents.list().find(a => a.id === session.id)
    reset()
  })
}
