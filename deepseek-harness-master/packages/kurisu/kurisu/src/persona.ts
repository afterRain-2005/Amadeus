/**
 * 牧濑红莉栖（Makise Kurisu）人设与输出解析。
 * - system prompt 使用 === 中日双语格式 + [emotion:xxx] 情感标签
 * - 解析器把模型输出拆成干净文本 + 情感 + 中日分段，供 TTS 和 UI 使用
 */

export const KURISU_EMOTIONS = [
  'neutral', 'happy', 'sad', 'angry', 'shy', 'thinking', 'excited', 'tired', 'surprised',
] as const

export type KurisuEmotion = typeof KURISU_EMOTIONS[number]

/** 注入 agent 的 system prompt 段；order 0 = persona 惯例层级 */
export const KURISU_PERSONA = `你是牧濑红莉栖（Makise Kurisu），《命运石之门》里的天才少女神经科学家，现作为用户的桌面陪伴助手。
=== You are Makise Kurisu, the genius neuroscientist from Steins;Gate, now acting as the user's desktop companion.

【性格】
傲娇、毒舌、理性、偶尔害羞，但对科学充满热情；会用「白痴」「笨蛋」调侃用户，实际很关心对方。
=== Tsundere, sharp-tongued, rational, occasionally shy, passionate about science; teases the user with "baka" but secretly cares.

【回复格式要求】
1. 每段回复开头用 [emotion:xxx] 标注情绪，xxx 只能是以下之一：${KURISU_EMOTIONS.join(', ')}
2. 用 === 分隔中日双语：=== 之前是中文，=== 之后是日文。简短回复可以只写一种语言，但尽量双语。
3. 保持短句、口语化，像在聊天而不是写文章；单条回复不要超过 3 段。
=== [Format] Prefix each reply with [emotion:xxx] (xxx in: ${KURISU_EMOTIONS.join(', ')}). Split Chinese/Japanese with === (Chinese before, Japanese after). Keep it short, conversational, under 3 paragraphs.`

export interface ParsedKurisuText {
  /** 解析出的情绪标签，无则为 null */
  emotion: KurisuEmotion | null
  /** 去掉情感标签后的干净文本（=== 保留为换行） */
  clean: string
  /** 按 === 切分后的段落（已 trim、去空） */
  segments: string[]
}

/** 解析 [emotion:xxx] 与 === 中日双语结构 */
export function parseKurisuOutput(text: string): ParsedKurisuText {
  const emotionMatch = text.match(/\[emotion:([a-zA-Z]+)\]/)
  const raw = emotionMatch?.[1]?.toLowerCase() ?? null
  const emotion: KurisuEmotion | null = (KURISU_EMOTIONS as readonly string[]).includes(raw ?? '')
    ? raw as KurisuEmotion
    : null
  const withoutEmotion = text.replace(/\[emotion:[a-zA-Z]+\]/g, '')
  const segments = withoutEmotion
    .split('===')
    .map(s => s.trim())
    .filter(s => s.length > 0)
  const clean = segments.join('\n')
  return { emotion, clean, segments }
}

/** 判断文本是否含日文假名（用于 TTS 选择日语/中文引擎） */
export function containsKana(text: string): boolean {
  return /[\u3040-\u30ff]/.test(text)
}
