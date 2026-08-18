/**
 * Kurisu 插件配置：通过 ctx.settings 注册命名空间，用户可在 Web 设置页或
 * cordis.yml 里覆盖。schema 用 schemastery，与 harness 其余插件一致。
 */
import z from '@deepseek-ai/schemastery'
import { settingsNamespace } from '@deepseek-ai/dsh-settings'

export interface KurisuConfig {
  /** 人设名字，用于 system prompt 与问候语 */
  personaName: string
  /** 是否启用 TTS 语音合成 */
  ttsEnabled: boolean
  /** TTS 引擎：sapi(离线兜底) / aliyun(阿里云 CosyVoice) / gpt-sovits */
  ttsEngine: 'sapi' | 'aliyun' | 'gpt-sovits'
  /** 阿里云 DashScope API Key（引擎为 aliyun 时使用） */
  aliyunApiKey: string
  /** GPT-SoVITS 合成服务的 base URL */
  gptSovitsUrl: string
  /** 指定语音名（SAPI voice / 阿里云 voice 等），留空用引擎默认 */
  voice: string
  /** 是否启用主动陪伴 */
  companionEnabled: boolean
  /** 用户静默多久后触发一次主动陪伴（毫秒） */
  companionIdleMs: number
  /** 是否注册桌面工具（截图/剪贴板/窗口枚举） */
  desktopToolsEnabled: boolean
}

export const KURISU_NS = settingsNamespace('kurisu')

export const KurisuConfigSchema: z<KurisuConfig> = z.object({
  personaName: z.string().default('牧濑红莉栖'),
  ttsEnabled: z.boolean().default(true),
  ttsEngine: z.union(['sapi', 'aliyun', 'gpt-sovits']).default('sapi'),
  aliyunApiKey: z.string().default(''),
  gptSovitsUrl: z.string().default(''),
  voice: z.string().default(''),
  companionEnabled: z.boolean().default(true),
  companionIdleMs: z.number().step(1).min(1000).default(60000),
  desktopToolsEnabled: z.boolean().default(true),
})

export const KURISU_DEFAULTS: KurisuConfig = {
  personaName: '牧濑红莉栖',
  ttsEnabled: true,
  ttsEngine: 'sapi',
  aliyunApiKey: '',
  gptSovitsUrl: '',
  voice: '',
  companionEnabled: true,
  companionIdleMs: 60000,
  desktopToolsEnabled: true,
}
