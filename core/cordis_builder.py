"""从 harness 设置动态生成 cordis 配置。

静态模板 ``runtime/cordis.full.yml`` 是"全功能开启"的基准。本模块按设置页的
``enable_*`` 开关裁剪对应插件段，并把 ``sandbox_mode`` / ``approval_policy`` 转成
harness 原生取值：

- sandbox_mode: ``read-only`` | ``workspace-write`` | ``danger-full-access``
- approval_policy: ``ask`` | ``never``

生成结果写入 ``data/harness/cordis.full.yml``；``harness_bridge`` 优先读它，否则
回退到内置全量模板。这样设置页的开关能真正生效，而不是只存进 config 不影响运行时。
"""
from __future__ import annotations

import sys
from pathlib import Path

_VALID_SANDBOX_MODES = ("read-only", "workspace-write", "danger-full-access")
_VALID_APPROVAL_POLICIES = ("ask", "never")


def harness_data_dir() -> Path:
    """用户可写的 harness 数据目录（生成的 cordis / workspace / sessions 落点）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data" / "harness"
    return Path(__file__).resolve().parent.parent / "data" / "harness"


def _pick(value: object, valid: tuple[str, ...], default: str) -> str:
    return str(value) if value in valid else default


# ── 始终挂载的核心区 ────────────────────────────────────────────────────────
_CORE = """\
# ── 1. SDK JSON-RPC 服务（必需 entry） ──
- id: sdk-jsonrpc-server
  name: '@deepseek-ai/dsh-sdk-jsonrpc-server'

# ── 2. LLM 适配器 ──
- id: llm-deepseek
  name: '@deepseek-ai/dsh-llm-deepseek'
  config:
    thinking: enabled
    reasoningEffort: max

# ── 3. 子进程 / bash 执行器 ──
- id: subprocess
  name: '@deepseek-ai/dsh-subprocess-local'

- id: bash
  name: '@deepseek-ai/dsh-bash-local'
  config:
    cwd: !!js process.env.DSH_CWD ?? process.cwd()
    timeoutMs: 60000

# ── 5. Agent Spine（核心：skills/goals/toolJobs/workspaceContext 全开） ──
- id: agent-spine
  name: '@deepseek-ai/dsh-agent-spine-demo'
  config:
    persona: !!js process.env.DSH_SYSTEM_PROMPT ?? 'You are a coding agent.'
    workspaceContext:
      maxBytes: 65536
    skills:
      enabled: true
    toolBash:
      enableRunInBackground: false
    toolJobs: {}
    goals: {}

# ── 6. 会话持久化 / 查询 ──
- id: sessions
  name: '@deepseek-ai/dsh-session-persistence-jsonl'
  config:
    root: !!js process.env.DSH_SESSION_ROOT ?? './.sessions'
    compression: !!js "process.env.DSH_SNAPSHOT === undefined ? 'zstd' : 'none'"

- id: session-checkpoints
  name: '@deepseek-ai/dsh-session-checkpoint-policy'

- id: session-query-sqlite
  name: '@deepseek-ai/dsh-session-query-sqlite'
  config:
    path: ':memory:'
    openAt: never

- id: session-projection
  name: '@deepseek-ai/dsh-session-projection'
"""

# 沙箱化文件系统 + 沙箱策略 + 审批（enable_sandbox=True）
_FS_SANDBOX = """\
# ── 7. 文件系统工具（沙箱化提供 fs 服务） ──
- id: fs-observation-policy
  name: '@deepseek-ai/dsh-fs-observation-policy'

- id: tool-fs
  name: '@deepseek-ai/dsh-tool-fs'

# ── 8. 沙箱 / 审批 ──
# 注意：闭包内无 bash-sandbox/pwsh-sandbox，bash 工具无法被沙箱约束；
# 只有 fs-sandbox 在闭包内，因此沙箱仅约束文件系统操作。
- id: sandbox
  name: '@deepseek-ai/dsh-sandbox-local'

- id: sandbox-policy
  name: '@deepseek-ai/dsh-sandbox-policy'
  config:
    mode: '{sandbox_mode}'
    workspaceRoot: !!js process.env.DSH_CWD ?? process.cwd()

- id: fs-sandbox
  name: '@deepseek-ai/dsh-fs-sandbox'
  config:
    cwd: !!js process.env.DSH_CWD ?? process.cwd()

- id: approval
  name: '@deepseek-ai/dsh-user-approval'
  config:
    policy: '{approval_policy}'
"""

# 非沙箱文件系统（enable_sandbox=False，等价 SDK 基线 fs-local）
_FS_LOCAL = """\
# ── 7. 文件系统工具（非沙箱，SDK 基线） ──
- id: fs-local
  name: '@deepseek-ai/dsh-fs-local'
  config:
    cwd: !!js process.env.DSH_CWD ?? process.cwd()

- id: fs-observation-policy
  name: '@deepseek-ai/dsh-fs-observation-policy'

- id: tool-fs
  name: '@deepseek-ai/dsh-tool-fs'
"""

# 子代理 spawn 基础（始终挂载）
_SUBAGENT_BASE = """\
# ── 9. 子代理（spawn 基础） ──
- id: subagent
  name: '@deepseek-ai/dsh-subagent'

- id: subagent-spawn-in-process
  name: '@deepseek-ai/dsh-subagent-spawn-in-process'
  config:
    providerName: spawn

- id: tool-subagent
  name: '@deepseek-ai/dsh-tool-subagent'
  config:
    provider: spawn
    toolName: subagent
    enableRunInBackground: false
"""

# 子代理 fork（enable_subagent_fork=True）
_SUBAGENT_FORK = """\
# ── 9b. 子代理 fork（后台/连续） ──
- id: subagent-fork-in-process
  name: '@deepseek-ai/dsh-subagent-fork-in-process'
  config:
    providerName: fork

- id: tool-subagent-fork
  name: '@deepseek-ai/dsh-tool-subagent'
  config:
    provider: fork
    toolName: subagent_fork
    backgroundMode: one-shot

- id: tool-subagent-control
  name: '@deepseek-ai/dsh-tool-subagent-control'

- id: tool-subagent-list-agents
  name: '@deepseek-ai/dsh-tool-subagent-control/list-agents'
"""

_TODO_METER_COMPACTION = """\
# ── 10. Todo / 计量 / 压缩 ──
- id: tool-todo
  name: '@deepseek-ai/dsh-tool-todo'
  config:
    allowParallelInProgress: true

- id: token-meter
  name: '@deepseek-ai/dsh-token-meter'

- id: compaction-basic
  name: '@deepseek-ai/dsh-compaction-basic'
  config:
    thresholdRatio: 0.8
    retainRatio: 0.16
    maxTokens: 8192
    compactionRetries: 1

- id: tool-result-pruner
  name: '@deepseek-ai/dsh-compaction-tool-result-pruner'
  config:
    thresholdChars: 8192
    headChars: 4096
    tailChars: 1024
"""

_WEB = """\
# ── 11. Web 搜索（DeepSeek 官方，fetch 保持关闭防 SSRF） ──
- id: web
  name: '@deepseek-ai/dsh-web'
  config:
    searchProvider: deepseek-official

- id: web-search-deepseek
  name: '@deepseek-ai/dsh-web-search-deepseek'
  config:
    apiKeyEnv: DEEPSEEK_API_KEY

- id: tool-web
  name: '@deepseek-ai/dsh-tool-web'
  config:
    fetch: false
    searchTimeoutMs: 60000
"""

_PLAN_MODE = """\
# ── 12. 计划模式 ──
- id: plan-mode
  name: '@deepseek-ai/dsh-plan-mode'
  config:
    section: |
          You are in plan mode. Stay in plan mode until exit_plan_mode succeeds or the user switches the session mode. Imperative language to implement changes means plan the implementation, not execute it. A user's conversational agreement — including an answer confirming something you asked — approves nothing and does not end plan mode; fold the confirmed decision into the plan and submit it through exit_plan_mode.

          Explore first. Use non-mutating reads, searches, static analysis, and checks to ground the plan in the actual repository. Do not edit or write files, change configuration, run formatters or code generation that rewrites tracked files, commit, or otherwise carry out the plan. Prefer existing functions and patterns over new machinery.

          The tool catalog stays the same across modes for request-cache stability. These plan-mode rules override any later tool description or guidance that suggests using mutation tools; those tools remain listed only to keep the request shape stable. Do not use todo_write to track this planning phase: it tracks implementation after an approved plan, while the plan itself belongs in exit_plan_mode.

          Resolve discoverable facts by inspection. Use ask_user_question only for user-owned choices or material ambiguity that inspection cannot answer. Do not ask the user where code lives or how current behavior works when you can find out.

          Make the plan decision-complete: state the goal and success criteria; group implementation changes by subsystem; identify public API, schema, and data-flow changes; cover edge cases, failure modes, tests, acceptance criteria, and explicit assumptions. Keep it concise enough to review but detailed enough that another engineer can implement it without making design decisions.

          When ready, call exit_plan_mode with the complete plan markdown, starting with a # title. Make exit_plan_mode the only and final tool call in that assistant response: it presents the plan for approval, and implementation begins only in a later step after approval. Do not paste the final plan as a plain reply or ask "should I proceed?" through prose or ask_user_question. If review rejects it, incorporate the feedback and present again. If the review channel is unavailable or aborted, stay in plan mode and ask the user to switch modes manually; do not proceed with implementation.
"""

_WORKFLOW = """\
# ── 13. 工作流 ──
- id: workflow-worker-thread
  name: '@deepseek-ai/dsh-workflow-worker-thread'
  config:
    provider: spawn

- id: tool-workflow
  name: '@deepseek-ai/dsh-tool-workflow'
"""

_EDITOR = """\
# ── 14. 编辑器 ──
- id: tool-str-replace-editor
  name: '@deepseek-ai/dsh-tool-str-replace-editor'
  config:
    maxOutputChars: 16000
"""

_COMMANDS = """\
# ── 15. 命令 ──
- id: commands
  name: '@deepseek-ai/dsh-commands'

- id: command-goal
  name: '@deepseek-ai/dsh-command-goal'
"""

_TIMEOUT_REPEAT = """\
# ── 16. 超时 / 重复提醒 ──
- id: timeout-policy
  name: '@deepseek-ai/dsh-tool-call-timeout-policy'

- id: repeat-tool-reminder
  name: '@deepseek-ai/dsh-repeat-tool-reminder'
  config:
    thresholds: [3, 5, 8]
    argumentsPreviewChars: 500
"""

_TERMINAL = """\
# ── 17. 持久终端（Windows 禁用 PTY；README 明确 PTY 需 POSIX） ──
- id: tool-bash-persistent
  name: '@deepseek-ai/dsh-tool-bash-persistent'
  disabled: !!js process.platform === 'win32'

- id: terminal
  name: '@deepseek-ai/dsh-terminal'
  disabled: !!js process.platform === 'win32'

- id: terminal-bash
  name: '@deepseek-ai/dsh-terminal-bash'
  disabled: !!js process.platform === 'win32'
"""

_HEADER = """\
# Amadeus DeepSeek Harness 配置（由设置页生成，勿手改）
# 依据 Amadeus 设置页 harness 开关动态裁剪；修改后重新保存设置即可。
"""


def build_cordis_yaml(harness_cfg: dict) -> str:
    """根据 harness 设置生成 cordis 配置 YAML 文本。

    不落盘，只返回字符串；落盘见 :func:`write_generated_cordis`。
    """
    sandbox_mode = _pick(
        harness_cfg.get("sandbox_mode", "workspace-write"),
        _VALID_SANDBOX_MODES,
        "workspace-write",
    )
    approval_policy = _pick(
        harness_cfg.get("approval_policy", "ask"),
        _VALID_APPROVAL_POLICIES,
        "ask",
    )

    parts: list[str] = [_HEADER, _CORE]

    if bool(harness_cfg.get("enable_sandbox", True)):
        parts.append(_FS_SANDBOX.format(sandbox_mode=sandbox_mode, approval_policy=approval_policy))
    else:
        parts.append(_FS_LOCAL)

    parts.append(_SUBAGENT_BASE)

    if bool(harness_cfg.get("enable_subagent_fork", True)):
        parts.append(_SUBAGENT_FORK)

    parts.append(_TODO_METER_COMPACTION)

    if bool(harness_cfg.get("enable_web", True)):
        parts.append(_WEB)

    if bool(harness_cfg.get("enable_plan_mode", True)):
        parts.append(_PLAN_MODE)

    if bool(harness_cfg.get("enable_workflow", True)):
        parts.append(_WORKFLOW)

    if bool(harness_cfg.get("enable_editor", True)):
        parts.append(_EDITOR)

    if bool(harness_cfg.get("enable_commands", True)):
        parts.append(_COMMANDS)

    parts.append(_TIMEOUT_REPEAT)

    if bool(harness_cfg.get("enable_terminal", False)):
        parts.append(_TERMINAL)

    return "\n\n".join(parts) + "\n"


def write_generated_cordis(harness_cfg: dict) -> Path:
    """生成并写入 ``data/harness/cordis.full.yml``，返回写入路径。"""
    target = harness_data_dir() / "cordis.full.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_cordis_yaml(harness_cfg), encoding="utf-8")
    return target
