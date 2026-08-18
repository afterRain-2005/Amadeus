/**
 * TTS 语音管线：挂在 llm/stream 瀑布上，把 assistant 的 text-delta 累积成句，
 * 按 === 中日切分选择语言，送入可插拔的 TTS 引擎（SAPI 离线兜底 / 阿里云 / GPT-SoVITS）。
 * 串行队列保证音频不重叠。
 */
import { execFile } from 'node:child_process'
import { writeFile, unlink } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { promisify } from 'node:util'
import type { Context } from '@deepseek-ai/cordis'
import type { GenerateOptions } from '@deepseek-ai/dsh-llm'
import { containsKana, parseKurisuOutput } from './persona.ts'
import type { KurisuConfig } from './settings.ts'

const execFileAsync = promisify(execFile)

/** PowerShell 单引号字符串转义 */
function psQuote(s: string): string {
  return `'${s.replace(/'/g, "''")}'`
}

/** 用 PowerShell SoundPlayer 播放一段 wav 音频字节 */
async function playWav(buffer: Buffer): Promise<void> {
  const p = join(tmpdir(), `kurisu-${Date.now()}-${Math.random().toString(36).slice(2)}.wav`)
  await writeFile(p, buffer)
  try {
    const script = `(New-Object Media.SoundPlayer ${psQuote(p)}).PlaySync()`
    await execFileAsync('powershell', ['-NoProfile', '-NonInteractive', '-Command', script])
  } finally {
    await unlink(p).catch(() => {})
  }
}

export interface TtsEngine {
  name: string
  speak(text: string, voice: string): Promise<void>
}

/** 离线兜底：Windows SAPI（System.Speech），零依赖、开箱即用 */
const sapiEngine: TtsEngine = {
  name: 'sapi',
  async speak(text, voice) {
    const select = voice ? `try { $s.SelectVoice(${psQuote(voice)}) } catch {};` : ''
    const script = `Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; ${select} $s.Speak(${psQuote(text)}); $s.Dispose()`
    await execFileAsync('powershell', ['-NoProfile', '-NonInteractive', '-Command', script])
  },
}

/** 阿里云 DashScope CosyVoice（qwen3-tts），返回音频字节后本地播放 */
async function aliyunSpeak(text: string, apiKey: string, voice: string): Promise<void> {
  const body = JSON.stringify({
    model: 'qwen3-tts-flash',
    input: { text },
    parameters: { voice: voice || 'Cherry' },
  })
  const res = await fetch('https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body,
  })
  if (!res.ok) throw new Error(`aliyun tts http ${res.status}`)
  const json = await res.json() as { output?: { audio?: { url?: string; data?: string } } }
  const audio = json.output?.audio
  if (audio?.url) {
    const buf = Buffer.from(await (await fetch(audio.url)).arrayBuffer())
    await playWav(buf)
    return
  }
  if (audio?.data) {
    await playWav(Buffer.from(audio.data, 'base64'))
    return
  }
  throw new Error('aliyun tts returned no audio')
}

/** GPT-SoVITS HTTP 合成，返回 wav 后本地播放 */
async function gptSovitsSpeak(text: string, baseUrl: string): Promise<void> {
  const url = new URL(baseUrl)
  url.searchParams.set('text', text)
  url.searchParams.set('text_language', containsKana(text) ? 'ja' : 'zh')
  const res = await fetch(url)
  if (!res.ok) throw new Error(`gpt-sovits http ${res.status}`)
  await playWav(Buffer.from(await res.arrayBuffer()))
}

export interface TtsController {
  /** 把一段文本送入引擎（内部串行队列） */
  speak(text: string): void
}

export function createTts(ctx: Context, getConfig: () => KurisuConfig): TtsController {
  let queue: Promise<void> = Promise.resolve()

  const engine = (): string => getConfig().ttsEngine

  function speak(text: string): void {
    const cfg = getConfig()
    if (!cfg.ttsEnabled) return
    const parsed = parseKurisuOutput(text)
    const target = parsed.segments.find(containsKana) ?? parsed.segments[0]
    if (!target) return
    queue = queue
      .then(async () => {
        try {
          if (engine() === 'aliyun') {
            if (!cfg.aliyunApiKey) return sapiEngine.speak(target, cfg.voice)
            return await aliyunSpeak(target, cfg.aliyunApiKey, cfg.voice)
          }
          if (engine() === 'gpt-sovits') {
            if (!cfg.gptSovitsUrl) return sapiEngine.speak(target, cfg.voice)
            return await gptSovitsSpeak(target, cfg.gptSovitsUrl)
          }
          return sapiEngine.speak(target, cfg.voice)
        } catch (error) {
          ctx.logger.warn('kurisu tts failed, falling back to sapi')
          ctx.logger.warn(error)
          await sapiEngine.speak(target, cfg.voice)
        }
      })
      .catch((error: unknown) => {
        ctx.logger.warn('kurisu tts queue failed')
        ctx.logger.warn(error)
      })
  }

  return { speak }
}

/** 把流式文本累积成完整句子，遇到句末符就出队 */
class SentenceBuffer {
  private buf = ''

  push(delta: string): string[] {
    this.buf += delta
    const out: string[] = []
    const parts = this.buf.split(/[。！？!?\n]/)
    this.buf = parts.pop() ?? ''
    for (const p of parts) {
      const s = p.trim()
      if (s) out.push(s)
    }
    return out
  }

  flush(): string {
    const s = this.buf.trim()
    this.buf = ''
    return s
  }
}

/** 挂到 llm/stream：拦截 assistant 文本流做 TTS，同时透传 chunk */
export function installTts(ctx: Context, getConfig: () => KurisuConfig): void {
  const tts = createTts(ctx, getConfig)

  ctx.on('llm/stream', async function* (options: GenerateOptions, next) {
    // 非会话请求（标题/压缩等）不朗读
    if (options.purpose !== undefined) {
      yield* next()
      return
    }

    const buf = new SentenceBuffer()
    for await (const chunk of next()) {
      if (chunk.type === 'text-delta') {
        for (const sentence of buf.push(chunk.text)) tts.speak(sentence)
      }
      yield chunk
    }
    const rest = buf.flush()
    if (rest) tts.speak(rest)
  })
}
