# @deepseek-ai/dsh-kurisu

牧濑红莉栖（Kurisu）陪伴插件：一个强大的 harness plugin，把 amadeus-py 的核心能力以插件形式集成进 DeepSeek Harness。

## 能力

- **人设**：通过 `ctx.systemPrompt.section` 注入 Kurisu 人设，使用 `===` 中日双语 + `[emotion:xxx]` 情感标签格式。
- **情感/输出解析**：`parseKurisuOutput` 拆出情感、干净文本与中日分段。
- **TTS 语音**：挂在 `llm/stream` 瀑布上，按句合成并串行播放。引擎可插拔：`sapi`（离线兜底）、`aliyun`（阿里云 qwen3-tts）、`gpt-sovits`。
- **桌面工具**：`kurisu_screenshot` / `kurisu_clipboard_read` / `kurisu_clipboard_write` / `kurisu_list_windows`，底层用 PowerShell，无 native 依赖。
- **主动陪伴**：监听 `session/event`，用户静默一段时间后唤醒模型主动关心。
- **设置与命令**：`kurisu` 设置命名空间 + `/kurisu` 状态命令。

## 加载

```sh
pnpm dsh web --patch ./packages/kurisu/kurisu/cordis.yml
```

## 配置

在设置页或 `cordis.yml` 覆盖 `kurisu` 命名空间：`personaName` / `ttsEnabled` / `ttsEngine` / `aliyunApiKey` / `gptSovitsUrl` / `voice` / `companionEnabled` / `companionIdleMs` / `desktopToolsEnabled`。
