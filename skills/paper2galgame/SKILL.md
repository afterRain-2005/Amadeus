---
name: paper2galgame
slug: paper2galgame
version: "1.0.3"
displayName: "Paper2Galgame"
summary: "把 PDF/DOCX/PPT/文本学习资料一键转化为互动视觉小说（galgame），让学习变游戏。Turn PDF/DOCX/PPT/text into interactive visual-novel (galgame) stories to make learning fun."
description: "把 PDF/DOCX/PPT/纯文本学习资料一键转化为互动视觉小说（galgame）故事；管理或搜索角色与背景；导入或查看存档；完成 Paper2Gal 账号认证。当用户提到 Paper2Gal、Paper2Galgame、把文档转成视觉小说、生成 galgame 故事时触发。Turn PDF, DOCX, PPT/PPTX, or text into interactive visual-novel stories; manage or search characters and backgrounds; import or list saves; and authenticate the Paper2Gal account. Trigger whenever the user mentions Paper2Gal, Paper2Galgame, paper-to-galgame conversion, or asks to create a visual novel from a document with Paper2Gal."
tags: [visual-novel, galgame, education, gamified-learning, storytelling]
license: MIT
homepage: "https://paper2gal.com"
metadata:
  version: "1.0.3"
agent_created: true
---

# Paper2Galgame

Set `SKILL_DIR` to the absolute path of the directory containing this `SKILL.md`, then use the cross-platform Node launcher. It requires Node.js 18 or newer and selects `P2G_CLI_PATH`, a checked-out CLI, a global installation, or the published npm package in that order:

```bash
SKILL_DIR="<absolute path to this skill directory>"
node "$SKILL_DIR/scripts/paper2gal.cjs" <command> [options]
```

Use the same `node <absolute-launcher-path>` form on Windows; do not invoke Bash or WSL. `P2G_CLI_PATH` must point to the CLI JavaScript file, not an npm `.cmd` shim.

## Authentication

Before the first account operation, run:

```bash
node "$SKILL_DIR/scripts/paper2gal.cjs" auth:status
```

If unauthenticated, run `auth:login`. Tell the user that Paper2Gal opens a browser for OAuth consent and explicitly announce this skill-caused pause. The login uses Authorization Code + S256 PKCE and saves a dedicated API token in `~/.paper2gal/config.json` with owner-only permissions. If the browser does not open or the page reports a missing `client_id` or `redirect_uri`, retry with `auth:login --no-browser` and open the complete URL printed by the CLI. Do not reconstruct or shorten that URL.

Never ask the user to paste a password or expose a token in chat. Do not use `--token` unless the user explicitly asks for ephemeral environment-based authentication. If browser SSO is unavailable on an older CLI, use `config:set-token` only after the user creates a token in Paper2Gal settings.

## Core workflow

1. Resolve every input file to an absolute path and verify it exists.
2. Run `auth:status`, then `characters:list` if the user did not choose a character.
3. For PDF or DOCX, default to `--parse-mode local`; use `smart` when the user requests intelligent parsing or local extraction is unsuitable. PPT/PPTX should use `smart`.
4. Use `--wait` when the user wants a completed result in the current task. Add `--save` only when the user asks to save it to their Paper2Gal account.
5. Return the story title, completion state, character, and save ID when present. Do not paste a huge raw JSON response unless requested.

Example:

```bash
node "$SKILL_DIR/scripts/paper2gal.cjs" stories:generate \
  --file "/absolute/path/paper.pdf" \
  --parse-mode local \
  --character RUKA \
  --blackboard \
  --wait \
  --save
```

## Local character assets

Prefer local files over external image URLs. Resolve each file to an absolute path and verify it exists before mutation. Character creation and updates support:

- `--avatar-file`
- `--neutral-file`
- `--angry-file`
- `--surprised-file`
- `--thinking-file`
- `--cg-file`, repeatable up to three times
- `--voice-file` for a local reference voice

Local character images require Paper2Gal CLI 0.2.1 or newer. Before the first image upload, inspect the CLI `--help` output and confirm it lists `--avatar-file`. If not, report that the selected CLI is too old and use `P2G_CLI_PATH` with a current checkout or ask the user to update the installed package; do not silently fall back to URL fields.

Local files take precedence over matching URL fields. On update, one or more local CG files replace the complete existing CG list. After every `characters:create` or `characters:update`, run `characters:get <id-or-characterId>` and verify the returned image fields before reporting success.

Example:

```bash
node "$SKILL_DIR/scripts/paper2gal.cjs" characters:update my_character \
  --avatar-file "/absolute/path/avatar.png" \
  --neutral-file "/absolute/path/neutral.webp" \
  --cg-file "/absolute/path/cg-1.png"
node "$SKILL_DIR/scripts/paper2gal.cjs" characters:get my_character
```

When only an external image is available, verify its HTTP status, response MIME type, decodability, dimensions, and visual content. Download it to a temporary local file and upload that file instead of saving an unverified hotlink. Do not trust a filename extension or search-result description as proof of image content.

## Commands

- Account: `me`, `auth:login`, `auth:status`, `auth:logout`, `config:get`
- Characters: `characters:list`, `characters:get`, `characters:create`, `characters:update`, `characters:delete`
- Community: `community:search`, `community:borrow`
- Backgrounds: `backgrounds:list`, `backgrounds:upload`, `backgrounds:delete`
- Saves: `saves:list`, `saves:import`
- Stories: `stories:generate`, `stories:poll`, `stories:status`

Prefer JSON output (the default) when another step must consume results. Use `--table` only for concise user-facing inspection.

## Mutation safeguards

Treat generation, character creation/update, borrowing, background upload, save import, and saving a generated story as normal requested writes. Require explicit confirmation immediately before deleting a character or background. A failed or timed-out generation must not be automatically resubmitted because doing so may consume quota; poll the returned job ID instead.

On HTTP 429, report the rate limit and retry timing when available. Do not loop aggressively. On generation responses containing `jobId`, prefer `stories:poll <jobId>` or `stories:status <jobId>` instead of submitting again.
