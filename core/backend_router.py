"""Router for chat / deepseek harness / codex."""
from __future__ import annotations

import json
from pathlib import Path
import re

import httpx

from config import AGENT_ROUTER_DEFAULTS, KURISU_OUTPUT_FORMAT, OPENCLAW_DEFAULTS

GUI_PATTERN = re.compile(r"打开|启动|关闭|点击|截个屏|截图|屏幕|窗口|鼠标|键盘|记事本")
AGENT_PATTERN = re.compile(r"搜索|查找|帮我搜|帮我写|整理|运行|执行|读.{0,8}文件|列出|下载|运行动命令|脚本|查一下|百度|google|联网|查天气|查新闻")
CHAT_HINT_PATTERN = re.compile(r"^(你好|您好|晚上好|早上好|下午好|谢谢|在吗|嗨+|喂+|好久不见|怎么了).*$")

GUI_NUDGE = "Please use operate_gui for this task."
_codex_session_started = False


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
) -> tuple[str, str]:
    global _codex_session_started
    from core.agent_client import run_local_run
    from core.codex_client import ensure_agents_md, run_codex_turn
    from core.deepseek_client import run_deepseek_turn
    from core.harness_bridge import run_harness_turn
    from core.hermes_launcher import ensure_gateway, read_profile_api_key
    from core.storage import APP_DIR

    router = {**AGENT_ROUTER_DEFAULTS, **(config.get("agent_router") or {})}
    mode = str(router.get("mode", "chat"))

    openclaw_cfg = dict(OPENCLAW_DEFAULTS)
    if isinstance(config.get("openclaw"), dict):
        openclaw_cfg.update(config["openclaw"])
    openclaw_enabled = bool(openclaw_cfg.get("enabled", False))

    if system_role == "companion":
        route = "chat"
    elif mode in ("chat", "harness", "hermes", "deepseek", "codex"):
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
                reply = run_hermes_run(
                    base_url=base_url,
                    api_key=api_key,
                    input_text=input_text,
                    instructions=KURISU_OUTPUT_FORMAT,
                    conversation_history=conversation_history,
                    session_id=str(hermes_cfg.get("session_id")),
                    on_delta=on_delta,
                    on_status=on_status,
                    on_approval=on_approval,
                )
                return reply, "hermes"
            except RuntimeError:
                on_status("Hermes fallback failed; using local chat（本地直连）")
        else:
            on_status("Hermes gateway unavailable; using local chat（本地直连）")

    elif route == "harness":
        harness_cfg = {**AGENT_ROUTER_DEFAULTS.get("harness", {}), **(config.get("harness") or {})}
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
                soul_md=soul_md,
                instructions=KURISU_OUTPUT_FORMAT,
                input_text=input_text,
                conversation_history=conversation_history,
                memories=memories,
                on_delta=on_delta,
                on_status=on_status,
                on_tool_event=on_tool_event,
                on_approval=on_approval,
            )
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
                memories=memories,
                on_delta=on_delta,
                on_status=on_status,
                on_approval=on_approval,
            )
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
            memories=memories,
            resume=_codex_session_started,
            sandbox=str(codex_cfg.get("sandbox", "read-only")),
            timeout=float(codex_cfg.get("timeout", 120)),
            on_delta=on_delta,
            on_status=on_status,
        )
        _codex_session_started = True
        return reply, "codex"

    text = input_text if route != "gui" else input_text + "\n" + GUI_NUDGE
    if not skip_history and conversation_history is not None:
        conversation_history.append({"role": "user", "content": input_text})
    instructions = KURISU_OUTPUT_FORMAT
    if inject_system_prompt:
        instructions = f"{inject_system_prompt}\n\n{KURISU_OUTPUT_FORMAT}"
    reply = run_local_run(
        endpoint=config.get("endpoint", ""),
        api_key=config.get("api_key", ""),
        model=config.get("model", ""),
        soul_md=soul_md,
        instructions=instructions,
        input_text=text,
        conversation_history=conversation_history,
        memories=memories,
        on_status=on_status,
        on_delta=on_delta,
        on_approval=on_approval,
    )
    return reply, ("gui" if route == "gui" else "chat")
