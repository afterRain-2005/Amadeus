"""Router for chat / deepseek harness / codex."""
from __future__ import annotations

import json
from pathlib import Path
import re

import httpx

from config import AGENT_ROUTER_DEFAULTS, KURISU_OUTPUT_FORMAT, MEMORY_DEFAULTS

GUI_PATTERN = re.compile(r"打开|启动|关闭|点击|截个屏|截图|屏幕|窗口|鼠标|键盘|记事本")
AGENT_PATTERN = re.compile(r"搜索|查找|帮我搜|帮我写|整理|运行|执行|读.{0,8}文件|列出|下载|运行动命令|脚本|查一下|百度|google|联网|查天气|查新闻")
CHAT_HINT_PATTERN = re.compile(r"^(你好|您好|晚上好|早上好|下午好|谢谢|在吗|嗨+|喂+|好久不见|怎么了).*$")

GUI_NUDGE = "Please use operate_gui for this task."
_codex_session_started = False


# ============================================================
# 函数：classify_input()
# 作用：把用户输入的文字分类成三种路由之一：
#       "chat"（普通聊天）/ "agent"（任务/工具）/ "gui"（桌面 GUI 操作）。
#       判断顺序：空输入→chat；短句打招呼→chat；含 GUI 关键词→gui；
#       含 agent 关键词→agent；都匹配不上→用 LLM 分类（可选）。
# 参数：
#   text             str  用户输入的原始文字
#   openclaw_enabled bool 是否启用 OpenClaw GUI 后端（默认 False）
#   llm_classify     callable|None 可选的 LLM 分类函数（入参 text，返回
#                    "chat"/"agent"/"gui" 或 None），None 时跳过 LLM 分类
# 返回值：str —— "chat" | "agent" | "gui" 之一
# ============================================================
def classify_input(text: str, *, openclaw_enabled: bool = False, llm_classify=None) -> str:
    text = (text or "").strip()
    if not text:
        return "chat"
    if len(text) <= 6 and CHAT_HINT_PATTERN.match(text):
        return "chat"
    if openclaw_enabled and GUI_PATTERN.search(text):
        return "gui"
    if AGENT_PATTERN.search(text):
        return "agent"
    if llm_classify is None:
        return "chat"
    try:
        result = llm_classify(text)
    except Exception:
        return "chat"
    return result if result in ("chat", "agent", "gui") else "chat"


# ============================================================
# 函数：_llm_classify()
# 作用：用远程 LLM（如 DeepSeek）把输入分类成 chat/agent/gui。
#       调用 OpenAI 兼容的 /chat/completions 接口，要求模型只返回
#       {"route":"..."} 的 JSON，再从中解析 route 字段。
#       任何异常（网络错误/解析失败/返回非法值）都返回 None，
#       由调用方回退到"chat"——失败要"漏回"最安全路径，不能崩。
# 参数：
#   text     str  用户输入
#   endpoint str  LLM 接口地址（OpenAI 兼容）
#   api_key  str  API 密钥
#   model    str  模型名
# 返回值：str | None —— "chat"/"agent"/"gui"，失败时返回 None
# ============================================================
def _llm_classify(text: str, *, endpoint: str, api_key: str, model: str) -> str | None:
    system = 'Return JSON only: {"route":"chat|agent|gui"}. chat for normal chat, agent for task/tool use, gui for desktop GUI actions.'
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{endpoint.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": text},
                    ],
                    "max_tokens": 50,
                    "temperature": 0,
                },
            )
        if resp.is_error:
            return None
        content = resp.json()["choices"][0]["message"]["content"]
        match = re.search(r"\{[^{}]*\}", content or "")
        if not match:
            return None
        route = json.loads(match.group(0)).get("route")
        return route if route in ("chat", "agent", "gui") else None
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        return None


# ============================================================
# 函数：route_and_send()
# 作用：★整个 AI 对话的路由总入口。根据配置和输入决定走哪个后端，
#       调用对应后端发消息，返回 (回复文本, 实际使用的路由名)。
#       路由决策顺序：
#       1. system_role=="companion"（主动问候）→ 强制 chat
#       2. auto_route 开启 → 用本地 Ollama 小模型分流 local/harness
#       3. mode 指定了具体后端 → 直接用该后端
#       4. 其他（auto 模式）→ classify_input 关键词/LLM 分类
#       各后端失败时回退到本地直连（run_local_run），保证用户总能收到回复。
# 参数：
#   config             dict  全局配置（含 agent_router 等子配置）
#   input_text         str   用户输入的文本
#   soul_md            str   角色人设文本（system prompt）
#   conversation_history list[dict]|None 对话历史
#   memories           list[dict]|None 长期记忆
#   on_delta           callable 流式输出回调（每吐一个字调用一次）
#   on_status          callable 状态提示回调（如"正在切换后端"）
#   on_tool_event      callable 工具执行事件回调（harness 用）
#   on_approval        callable 工具审批回调（默认一律拒绝）
#   system_role        str   调用角色："user"=用户聊天；"companion"=主动问候
#   skip_history       bool  是否不把本次输入追加进历史
#   inject_system_prompt str|None 额外叠加的系统提示词
# 返回值：tuple[str, str] —— (AI 回复文本, 实际路由名)
#         路由名可能是 "chat"/"agent"/"gui"/"hermes"/"harness"/"deepseek"/"codex"
# ============================================================
def route_and_send(
    *,
    config: dict,
    input_text: str,
    soul_md: str,
    conversation_history: list[dict] | None = None,
    memories: list[dict] | None = None,
    on_delta=lambda t: None,
    on_status=lambda t: None,
    on_tool_event=lambda e: None,
    on_approval=lambda p: "deny",
    system_role: str = "user",
    skip_history: bool = False,
    inject_system_prompt: str | None = None,
    response_max_tokens: int | None = 700,
    cancel_event=None,
    harness_session_id: str | None = None,
) -> tuple[str, str]:
    global _codex_session_started
    from core.agent_client import run_local_run
    from core.codex_client import ensure_agents_md, run_codex_turn
    from core.deepseek_client import run_deepseek_turn
    from core.harness_bridge import run_harness_turn
    from core.hermes_launcher import ensure_gateway, read_profile_api_key
    from core.memory import merge_memories, recall, remember_turn
    from core.openclaw_client import merge_config as merge_openclaw_config
    from core.storage import APP_DIR

    router = {**AGENT_ROUTER_DEFAULTS, **(config.get("agent_router") or {})}
    mode = str(router.get("mode", "chat"))
    memory_cfg = {**MEMORY_DEFAULTS, **(config.get("memory") if isinstance(config.get("memory"), dict) else {})}
    memory_enabled = bool(memory_cfg.get("enabled", True))
    memory_scope = str(memory_cfg.get("scope", "global") or "global")
    if memory_enabled:
        from core.memory import semantic_config
        recalled_memories = recall(
            query=input_text,
            limit=int(memory_cfg.get("recall_limit", 8) or 8),
            scope=memory_scope,
            semantic=semantic_config(config),
        )
        route_memories = merge_memories(memories, recalled_memories)
    else:
        route_memories = memories or []

    openclaw_cfg = merge_openclaw_config(config)
    openclaw_enabled = bool(openclaw_cfg.get("enabled", False))

    auto_route = bool(router.get("auto_route", False))
    if system_role == "companion":
        route = "chat"
    elif auto_route:
        from core.ollama_router import route_with_ollama
        ollama_cfg = dict(router.get("ollama") or {})
        try:
            ollama_timeout = float(ollama_cfg.get("timeout", 30))
        except (TypeError, ValueError):
            ollama_timeout = 30.0
        route = route_with_ollama(
            input_text,
            targets=list(router.get("auto_targets") or ["local", "harness"]),
            base_url=str(ollama_cfg.get("base_url", "http://127.0.0.1:11434")),
            model=str(ollama_cfg.get("model", "qwen2.5:0.5b")),
            timeout=ollama_timeout,
        )
    elif mode in ("chat", "harness", "hermes", "deepseek", "codex", "openclaw"):
        route = mode
    else:
        route = classify_input(
            input_text,
            openclaw_enabled=openclaw_enabled,
            llm_classify=lambda t: _llm_classify(
                t,
                endpoint=config.get("endpoint", ""),
                api_key=config.get("api_key", ""),
                model=config.get("model", ""),
            ),
        )

    if route == "hermes":
        hermes_cfg = {
            **{"base_url": "http://127.0.0.1:8642", "profile": "kurisu", "session_id": "amadeus-kurisu", "api_key": ""},
            **(config.get("hermes") or {}),
        }
        base_url = str(hermes_cfg.get("base_url"))
        api_key = str(hermes_cfg.get("api_key") or "") or (read_profile_api_key(str(hermes_cfg.get("profile"))) or "")
        if ensure_gateway(base_url=base_url, api_key=api_key, profile=str(hermes_cfg.get("profile"))):
            try:
                from core.agent_client import run_hermes_run
                instructions = KURISU_OUTPUT_FORMAT
                if route_memories:
                    memory_text = "\n".join(str(item.get("content", "")) for item in route_memories[-8:])
                    if memory_text.strip():
                        instructions += f"\n\nMemory:\n{memory_text}"
                reply = run_hermes_run(
                    base_url=base_url,
                    api_key=api_key,
                    input_text=input_text,
                    instructions=instructions,
                    conversation_history=conversation_history,
                    session_id=str(hermes_cfg.get("session_id")),
                    on_delta=on_delta,
                    on_status=on_status,
                    on_approval=on_approval,
                )
                if memory_enabled:
                    remember_turn(user_text=input_text, assistant_text=reply, source="hermes", scope=memory_scope)
                return reply, "hermes"
            except RuntimeError:
                on_status("Hermes fallback failed; using local chat（本地直连）")
        else:
            on_status("Hermes gateway unavailable; using local chat（本地直连）")

    elif route == "openclaw":
        # 整轮对话委托 OpenClaw 代理（skills/浏览器/CUA 等能力由其 agent loop 处理）
        from core.openclaw_client import ensure_gateway as ensure_openclaw_gateway
        from core.openclaw_client import run_openclaw_turn
        base_url = str(openclaw_cfg.get("base_url", "http://127.0.0.1:18789"))
        token = str(openclaw_cfg.get("token", ""))
        instructions = KURISU_OUTPUT_FORMAT
        if inject_system_prompt:
            instructions = f"{KURISU_OUTPUT_FORMAT}\n\n{inject_system_prompt}"
        try:
            if not ensure_openclaw_gateway(
                base_url=base_url, token=token,
                autostart=bool(openclaw_cfg.get("autostart", True)),
            ):
                raise RuntimeError(f"gateway unreachable: {base_url}")
            reply = run_openclaw_turn(
                base_url=base_url,
                token=token,
                model=str(openclaw_cfg.get("model", "openclaw/default")),
                soul_md=soul_md,
                instructions=instructions,
                input_text=input_text,
                conversation_history=conversation_history,
                memories=route_memories,
                on_delta=on_delta,
            )
            if memory_enabled:
                remember_turn(user_text=input_text, assistant_text=reply, source="openclaw", scope=memory_scope)
            return reply, "openclaw"
        except RuntimeError as exc:
            on_status(f"OpenClaw gateway failed ({exc}); using local chat（本地直连）")

    elif route == "harness":
        harness_cfg = {**AGENT_ROUTER_DEFAULTS.get("harness", {}), **(config.get("harness") or {})}
        instructions = KURISU_OUTPUT_FORMAT
        if inject_system_prompt:
            # inject 放 KURISU 之后：后置指令服从性更高（电话模式靠 OVERRIDES 覆盖格式）
            instructions = f"{KURISU_OUTPUT_FORMAT}\n\n{inject_system_prompt}"
        try:
            reply = run_harness_turn(
                endpoint=str(harness_cfg.get("base_url", "") or config.get("endpoint", "")),
                api_key=str(harness_cfg.get("api_key", "") or config.get("api_key", "")),
                model=str(harness_cfg.get("model", "") or config.get("model", "") or "deepseek-v4-flash"),
                provider=str(harness_cfg.get("provider", "deepseek-official")),
                runtime_bin=str(harness_cfg.get("runtime_bin", "") or ""),
                cordis=str(harness_cfg.get("cordis", "") or ""),
                cwd=str(harness_cfg.get("cwd", "") or ""),
                session_root=str(harness_cfg.get("session_root", "") or ""),
                request_timeout_seconds=float(harness_cfg.get("request_timeout_seconds", 180)),
                session_id=harness_session_id,
                soul_md=soul_md,
                instructions=instructions,
                input_text=input_text,
                conversation_history=conversation_history,
                memories=route_memories,
                on_delta=on_delta,
                on_status=on_status,
                on_tool_event=on_tool_event,
                on_approval=on_approval,
            )
            if memory_enabled:
                remember_turn(user_text=input_text, assistant_text=reply, source="harness", scope=memory_scope)
            return reply, "harness"
        except RuntimeError as exc:
            on_status(f"Harness failed: {exc}")

    elif route == "deepseek":
        deepseek_cfg = {**AGENT_ROUTER_DEFAULTS["deepseek"], **(config.get("deepseek") or {})}
        try:
            reply = run_deepseek_turn(
                endpoint=str(deepseek_cfg.get("base_url", "http://127.0.0.1:8642")),
                api_key=str(deepseek_cfg.get("api_key") or ""),
                model=str(deepseek_cfg.get("model") or "deepseek-v3.1"),
                soul_md=soul_md,
                instructions=KURISU_OUTPUT_FORMAT,
                input_text=input_text,
                conversation_history=conversation_history,
                memories=route_memories,
                on_delta=on_delta,
                on_status=on_status,
                on_approval=on_approval,
            )
            if memory_enabled:
                remember_turn(user_text=input_text, assistant_text=reply, source="deepseek", scope=memory_scope)
            return reply, "deepseek"
        except RuntimeError as exc:
            on_status(f"DeepSeek harness failed: {exc}")

    elif route == "codex":
        codex_cfg = {**AGENT_ROUTER_DEFAULTS["codex"], **(router.get("codex") or {})}
        workspace = Path(str(codex_cfg.get("workspace", "data/codex_workspace")))
        if not workspace.is_absolute():
            workspace = APP_DIR.parent / workspace
        ensure_agents_md(workspace, soul_md, KURISU_OUTPUT_FORMAT)
        reply = run_codex_turn(
            input_text=input_text,
            workspace=workspace,
            conversation_history=conversation_history,
            memories=route_memories,
            resume=_codex_session_started,
            sandbox=str(codex_cfg.get("sandbox", "read-only")),
            timeout=float(codex_cfg.get("timeout", 120)),
            on_delta=on_delta,
            on_status=on_status,
        )
        _codex_session_started = True
        if memory_enabled:
            remember_turn(user_text=input_text, assistant_text=reply, source="codex", scope=memory_scope)
        return reply, "codex"

    text = input_text if route != "gui" else input_text + "\n" + GUI_NUDGE
    if not skip_history and conversation_history is not None:
        conversation_history.append({"role": "user", "content": input_text})
    instructions = KURISU_OUTPUT_FORMAT
    if inject_system_prompt:
        # inject 放 KURISU 之后：后置指令服从性更高（电话模式靠 OVERRIDES 覆盖格式）
        instructions = f"{KURISU_OUTPUT_FORMAT}\n\n{inject_system_prompt}"
    reply = run_local_run(
        endpoint=config.get("endpoint", ""),
        api_key=config.get("api_key", ""),
        model=config.get("model", ""),
        soul_md=soul_md,
        instructions=instructions,
        input_text=text,
        conversation_history=conversation_history,
        memories=route_memories,
        on_status=on_status,
        on_delta=on_delta,
        on_tool_event=on_tool_event,
        on_approval=on_approval,
        max_tokens=response_max_tokens,
        cancel_event=cancel_event,
    )
    if memory_enabled:
        remember_turn(user_text=input_text, assistant_text=reply, source=("gui" if route == "gui" else "chat"), scope=memory_scope)
    return reply, ("gui" if route == "gui" else "chat")
