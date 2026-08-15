"""gate 分流 + 后端路由（spec §3 / §4.1）。

route_and_send 是 desktop_pet.AgentTask 的唯一入口：
按 agent_router.mode 分发到 chat(本地直连) / hermes / codex；
auto 模式先 classify_input（L1 规则 → L2 DeepSeek 意图分类）。
返回 (reply, backend_used)，hermes 失败自动降级本地直连。
"""
from __future__ import annotations

import json
from pathlib import Path
import re

import httpx

from config import AGENT_ROUTER_DEFAULTS, KURISU_OUTPUT_FORMAT, OPENCLAW_DEFAULTS

GUI_PATTERN = re.compile(
    r"打开|关闭|点击|截.?屏|截.?图|鼠标|键盘|双击|右键|操作.{0,6}(窗口|界面|软件|应用)")
AGENT_PATTERN = re.compile(
    r"搜索|查找文件|帮我(写|整理|运行|分析|找|搜|查)|读.{0,8}文件|列出|下载|"
    r"运行(命令|脚本)|查一下|百度|google|联网|查天气|查新闻")
CHAT_HINT_PATTERN = re.compile(
    r"^(你好|早上好|中午好|晚上好|嗨|哈喽|在吗|嗯+|哦+|好呀?|晚安|再见|无聊|随便聊聊).*$")

GUI_NUDGE = "（用户想操作图形界面/应用，优先调用 operate_gui 工具完成。）"

_codex_session_started = False  # codex resume --last 会话状态（进程内）


def classify_input(text: str, *, openclaw_enabled: bool = False, llm_classify=None) -> str:
    """L1 规则 → L2 LLM（可注入）。返回 'chat' | 'agent' | 'gui'。失败默认 chat。"""
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
    """DeepSeek 意图分类（非流式小请求）。失败返回 None。"""
    system = (
        "你是输入分流器。把用户输入分类为 JSON：{\"route\":\"chat|agent|gui\"}。"
        "chat=闲聊/问答；agent=需要工具/搜索/文件/命令；gui=需要操作图形界面（鼠标键盘窗口）。"
        "只输出 JSON。"
    )
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{endpoint.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ], "max_tokens": 50, "temperature": 0},
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
    on_approval=lambda p: "deny",
    system_role: str = "user",
    skip_history: bool = False,
    inject_system_prompt: str | None = None,
) -> tuple[str, str]:
    """按模式分发，返回 (reply, backend_used)。hermes 失败自动降级本地直连。

    扩展参数（companion 用）：
    - system_role="companion" 跳过 classify_input，直接走 chat 路径
    - skip_history=True 时不写 conversation_history
    - inject_system_prompt 注入到 messages 最前，作为额外 system 指令
    """
    global _codex_session_started
    from core.agent_client import run_hermes_run, run_local_run
    from core.codex_client import ensure_agents_md, run_codex_turn
    from core.hermes_launcher import ensure_gateway, read_profile_api_key
    from core.storage import APP_DIR

    router = {**AGENT_ROUTER_DEFAULTS, **(config.get("agent_router") or {})}
    mode = str(router.get("mode", "chat"))

    openclaw_cfg = dict(OPENCLAW_DEFAULTS)
    if isinstance(config.get("openclaw"), dict):
        openclaw_cfg.update(config["openclaw"])
    openclaw_enabled = bool(openclaw_cfg.get("enabled", False))

    # companion 模式：跳过 classify_input，直接走 chat 路径
    if system_role == "companion":
        route = "chat"
    elif mode in ("chat", "hermes", "codex"):
        route = mode
    else:
        route = classify_input(
            input_text, openclaw_enabled=openclaw_enabled,
            llm_classify=lambda t: _llm_classify(
                t, endpoint=config.get("endpoint", ""),
                api_key=config.get("api_key", ""), model=config.get("model", "")),
        )

    hermes_cfg = {**{"base_url": "http://127.0.0.1:8642", "profile": "kurisu",
                     "session_id": "amadeus-kurisu", "api_key": ""},
                  **(config.get("hermes") or {})}
    base_url = str(hermes_cfg.get("base_url"))
    api_key = str(hermes_cfg.get("api_key") or "") \
        or (read_profile_api_key(str(hermes_cfg.get("profile"))) or "")

    if route == "hermes":
        if ensure_gateway(base_url=base_url, api_key=api_key,
                          profile=str(hermes_cfg.get("profile"))):
            try:
                reply = run_hermes_run(
                    base_url=base_url, api_key=api_key, input_text=input_text,
                    instructions=KURISU_OUTPUT_FORMAT,
                    conversation_history=conversation_history,
                    session_id=str(hermes_cfg.get("session_id")),
                    on_delta=on_delta, on_status=on_status, on_approval=on_approval,
                )
                return reply, "hermes"
            except RuntimeError:
                on_status("Hermes 调用失败，已切本地直连")
        else:
            on_status("Hermes 网关不可用，已切本地直连")

    elif route == "codex":
        codex_cfg = {**AGENT_ROUTER_DEFAULTS["codex"], **(router.get("codex") or {})}
        workspace = Path(str(codex_cfg.get("workspace", "data/codex_workspace")))
        if not workspace.is_absolute():
            workspace = APP_DIR.parent / workspace  # APP_DIR=<根>/data → 取项目根
        ensure_agents_md(workspace, soul_md, KURISU_OUTPUT_FORMAT)
        reply = run_codex_turn(
            input_text=input_text, workspace=workspace,
            resume=_codex_session_started,
            sandbox=str(codex_cfg.get("sandbox", "read-only")),
            timeout=float(codex_cfg.get("timeout", 120)),
            on_delta=on_delta, on_status=on_status)
        _codex_session_started = True
        return reply, "codex"

    # chat / gui / hermes 降级：本地直连（gui 追加 operate_gui 引导）
    text = input_text if route != "gui" else input_text + "\n" + GUI_NUDGE
    # companion 模式不写 conversation_history
    if not skip_history and conversation_history is not None:
        conversation_history.append({"role": "user", "content": input_text})
    # inject_system_prompt 叠加到 instructions
    instructions = KURISU_OUTPUT_FORMAT
    if inject_system_prompt:
        instructions = f"{inject_system_prompt}\n\n{KURISU_OUTPUT_FORMAT}"
    reply = run_local_run(
        endpoint=config.get("endpoint", ""), api_key=config.get("api_key", ""),
        model=config.get("model", ""), soul_md=soul_md,
        instructions=instructions, input_text=text,
        conversation_history=conversation_history, memories=memories,
        on_status=on_status, on_delta=on_delta, on_approval=on_approval)
    return reply, ("gui" if route == "gui" else "chat")
