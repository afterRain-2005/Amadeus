"""DeepSeek Harness SDK 桥接模块。

复用 deepseek-harness 的 Python SDK 作为 agent 后端：
- 通过 JSON-RPC over stdio 启动 harness 运行时子进程（Node.js）
- Windows 上使用 dev-only 的 node 闭包（生产 exe 仅 linux/macos）
- SDK 不可用或运行时启动失败时，自动回退到 core/deepseek_client.py 直连
"""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

# 将 deepseek-harness Python SDK + 运行时加入 sys.path（未全局安装时也能用）
_HARNESS_ROOT = Path(__file__).resolve().parent.parent / "deepseek-harness-master"
_HARNESS_SDK_DIR = _HARNESS_ROOT / "python" / "sdk" / "src"
_HARNESS_RUNTIME_DIR = _HARNESS_ROOT / "python" / "sdk-runtime" / "src"
for _d in (_HARNESS_SDK_DIR, _HARNESS_RUNTIME_DIR):
    if _d.exists() and str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

_HARNESS_AVAILABLE = False
_HARNESS_IMPORT_ERROR: str | None = None

try:
    from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig, RunResult
    from deepseek_harness.client import Notification
    _HARNESS_AVAILABLE = True
except ImportError as e:
    _HARNESS_AVAILABLE = False
    _HARNESS_IMPORT_ERROR = str(e)


# 当前正在运行的 harness turn（中断用）。harness.run() 是同步阻塞调用，跑在
# AgentTask 工作线程；UI 主线程通过 cancel_active_run() 拿到这里的 session_id
# 与实例，再经 JSON-RPC session/cancel 让运行时中止本轮。
_active_lock = threading.Lock()
_active_session_id: str | None = None
_active_harness: DeepSeekHarness | None = None


def cancel_active_run() -> bool:
    """从任意线程中断当前正在运行的 harness turn。

    工作线程阻塞在 harness.run() 的 notification 循环期间，UI 主线程调用本函数；
    客户端 transport 的写侧有独立锁，因此与 run 循环并发安全。返回是否确实发起中断。
    """
    with _active_lock:
        session_id = _active_session_id
        harness = _active_harness
    if session_id is None or harness is None:
        return False
    try:
        harness.cancel(session_id)
        return True
    except Exception:
        return False


def _wrap_harness_approval(on_approval: Callable[[dict], str]) -> Callable[[dict], str]:
    """把 harness 审批词表转成 Amadeus 内部审批词表。

    harness 的 ApprovalOutcome 只有 ``allowed-once`` 是放行，没有 once/session/always
    的「记住」语义；本函数把 harness 载荷 ``{toolName, callId, reason}`` 映射成
    Amadeus ``_confirm_operation`` 认识的 ``{command, description, pattern_key}``，
    并把返回值：
      once/session/always -> allowed-once（harness 无「记住」，统一按放行一次处理）
      deny                 -> rejected
      其余/回调异常         -> unavailable（fail-closed）
    """
    def _decide(payload: dict) -> str:
        tool_name = str(payload.get("toolName", ""))
        reason = str(payload.get("reason", "") or tool_name)
        amadeus_payload = {
            "command": tool_name,
            "description": reason,
            "pattern_key": str(payload.get("callId", "")),
            "pattern_keys": [],
            "choices": ["once", "session", "always", "deny"],
        }
        try:
            choice = on_approval(amadeus_payload)
        except Exception:
            return "unavailable"
        if choice in ("once", "session", "always"):
            return "allowed-once"
        if choice == "deny":
            return "rejected"
        return "unavailable"
    return _decide


def _runtime_data_dir() -> Path:
    """定位 deepseek_harness_runtime 数据目录。

    源码运行时用仓库内路径；PyInstaller 打包后从 sys._MEIPASS 解包目录读取
    （Amadeus.spec 会把整个 deepseek_harness_runtime 目录作为 datas 打入）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "deepseek_harness_runtime"
    return _HARNESS_ROOT / "python" / "sdk-runtime" / "src" / "deepseek_harness_runtime"


def _resolve_launch_args() -> tuple[str, ...]:
    """解析 harness 运行时启动参数。

    Windows 上生产 exe 不存在，使用 dev-only 的 node 闭包（系统需有 Node）。
    """
    if os.name == "nt":
        import shutil
        node = shutil.which("node")
        if node is None:
            raise FileNotFoundError("harness node 模式需要系统 Node.js (>=22.19) 在 PATH 中")
        bin_js = (
            _runtime_data_dir()
            / "runtime" / "node" / "node_modules" / "@deepseek-ai"
            / "dsh-sdk-jsonrpc-demo" / "lib" / "packaged-bin.js"
        )
        if not bin_js.is_file():
            raise FileNotFoundError(f"harness node 闭包缺失: {bin_js}")
        return (node, str(bin_js))
    # 非 Windows：使用 SDK 自动解析（生产 exe）
    from deepseek_harness_runtime import resolve_bundled_launch_args
    return resolve_bundled_launch_args()


def _default_cordis() -> str:
    # 1. 用户生成的配置（设置页保存时写入 data/harness/cordis.full.yml）
    user_full = _default_harness_root() / "cordis.full.yml"
    if user_full.is_file():
        return str(user_full)
    # 2. 内置全量模板（打包进 deepseek_harness_runtime）
    full_path = _runtime_data_dir() / "runtime" / "cordis.full.yml"
    if full_path.is_file():
        return str(full_path)
    # 3. SDK 基线
    path = _runtime_data_dir() / "runtime" / "cordis.yml"
    if not path.is_file():
        raise FileNotFoundError(f"harness 默认配置缺失: {path}")
    return str(path)


def _default_harness_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data" / "harness"
    return _HARNESS_ROOT.parent / "data" / "harness"


def _resolve_harness_path(value: str | None, fallback_name: str) -> Path:
    if value:
        path = Path(value)
        if not path.is_absolute():
            path = Path.cwd() / path
        return path
    return _default_harness_root() / fallback_name


def _extract_tool_content(content: object) -> str:
    """从 tool/result 的 content 块中提取纯文本（text 块拼接）。"""
    if not isinstance(content, list):
        return str(content) if content else ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(parts)


def run_harness_turn(
    *,
    endpoint: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    runtime_bin: str | None = None,
    cordis: str | None = None,
    cwd: str | None = None,
    session_root: str | None = None,
    request_timeout_seconds: float | None = None,
    soul_md: str,
    instructions: str,
    input_text: str,
    conversation_history: list[dict] | None = None,
    memories: list[dict] | None = None,
    on_delta: Callable[[str], None] = lambda _: None,
    on_status: Callable[[str], None] = lambda _: None,
    on_tool_event: Callable[[dict], None] = lambda _: None,
    on_approval: Callable[[dict], str] = lambda _: "deny",
) -> str:
    """使用 DeepSeek Harness SDK 运行一个 agent turn。

    SDK 启动 JSON-RPC 运行时子进程通信，支持完整 agent 工具链。
    若 SDK 运行时不可用，回退到 core/deepseek_client.py 直连。
    """
    if _HARNESS_AVAILABLE:
        try:
            return _run_via_sdk(
                endpoint=endpoint,
                api_key=api_key,
                model=model,
                provider=provider,
                runtime_bin=runtime_bin,
                cordis=cordis,
                cwd=cwd,
                session_root=session_root,
                request_timeout_seconds=request_timeout_seconds,
                soul_md=soul_md,
                instructions=instructions,
                input_text=input_text,
                conversation_history=conversation_history,
                memories=memories,
                on_delta=on_delta,
                on_status=on_status,
                on_tool_event=on_tool_event,
                on_approval=on_approval,
            )
        except (FileNotFoundError, RuntimeError) as exc:
            msg = str(exc)
            if any(k in msg.lower() for k in ("bundled", "runtime", "node", "closure", "missing")):
                on_status(f"Harness 运行时不可用，回退到直连: {exc}")
            else:
                raise

    on_status("Harness SDK 不可用，使用 DeepSeek 直连")
    from core.deepseek_client import run_deepseek_turn

    return run_deepseek_turn(
        endpoint=endpoint or "http://127.0.0.1:8642",
        api_key=api_key or "",
        model=model or "deepseek-v3.1",
        soul_md=soul_md,
        instructions=instructions,
        input_text=input_text,
        conversation_history=conversation_history,
        memories=memories,
        on_delta=on_delta,
        on_status=on_status,
        on_approval=on_approval,
    )


def _run_via_sdk(
    *,
    endpoint: str | None,
    api_key: str | None,
    model: str | None,
    provider: str | None,
    runtime_bin: str | None,
    cordis: str | None,
    cwd: str | None,
    session_root: str | None,
    request_timeout_seconds: float | None,
    soul_md: str,
    instructions: str,
    input_text: str,
    conversation_history: list[dict] | None,
    memories: list[dict] | None,
    on_delta: Callable[[str], None],
    on_status: Callable[[str], None],
    on_tool_event: Callable[[dict], None],
    on_approval: Callable[[dict], str],
) -> str:
    global _active_session_id, _active_harness

    # 组装 system prompt
    system = soul_md + "\n\n" + instructions
    if memories:
        memory_text = "\n".join(str(item.get("content", "")) for item in memories[-8:])
        if memory_text.strip():
            system += f"\n\nMemory:\n{memory_text}"

    # 组装会话上下文
    history_parts: list[str] = []
    if conversation_history:
        for item in conversation_history[-14:]:
            role = item.get("role", "user")
            content = item.get("content", "")
            if content:
                history_parts.append(f"**{role}**: {content}")
    if history_parts:
        system += "\n\n## Conversation History\n" + "\n".join(history_parts)

    # 运行时启动参数：Windows 用 node 闭包
    launch_args = _resolve_launch_args()

    # 工作区/会话目录必须在启动前存在：DeepSeekHarnessConfig.cwd 同时被用作
    # runtime_cwd（Popen 子进程 cwd），目录不存在会导致 WinError 267。
    workspace_dir = _resolve_harness_path(cwd, "workspace")
    sessions_dir = _resolve_harness_path(session_root, "sessions")
    workspace_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)

    # cordis：优先用户显式配置，否则回退到内置 cordis.full.yml。
    resolved_cordis = cordis or _default_cordis()

    config = DeepSeekHarnessConfig(
        provider=provider or "deepseek-official",
        model=model or "deepseek-v4-flash",
        base_url=endpoint or None,
        api_key=api_key or None,
        runtime_bin=runtime_bin or None,
        cwd=str(workspace_dir),
        session_root=str(sessions_dir),
        cordis=resolved_cordis,
        launch_args_override=launch_args or None,
        request_timeout_seconds=float(request_timeout_seconds or 180),
    )

    call_names: dict[str, str] = {}

    def handle_notification(notification: Notification) -> None:
        method = notification.method
        payload = notification.payload
        if method != "session.event":
            return
        event = payload.get("event")
        if not isinstance(event, dict):
            return
        event_type = event.get("type", "")
        data = event.get("data")
        data = data if isinstance(data, dict) else {}

        if event_type == "assistant/message":
            message = data.get("message")
            content = message.get("content") if isinstance(message, dict) else data.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = str(block.get("text", ""))
                        if text:
                            on_delta(text)
        elif event_type in ("tool/start", "tool/call"):
            name = str(data.get("name") or data.get("tool") or "tool")
            call_id = str(data.get("callId", ""))
            args_raw = data.get("arguments")
            args_dict: dict = {}
            if isinstance(args_raw, str):
                try:
                    parsed = json.loads(args_raw)
                    if isinstance(parsed, dict):
                        args_dict = parsed
                except Exception:
                    args_dict = {}
            elif isinstance(args_raw, dict):
                args_dict = args_raw
            if call_id:
                call_names[call_id] = name
            on_status(f"正在执行 {name}…")
            on_tool_event({
                "kind": "tool_call",
                "callId": call_id,
                "name": name,
                "arguments": args_dict,
                "argumentsRaw": args_raw,
            })
        elif event_type in ("tool/end", "tool/result"):
            call_id = str(data.get("callId", ""))
            name = call_names.get(call_id, str(data.get("name") or data.get("tool") or "tool"))
            is_error = bool(data.get("isError", False))
            content_text = _extract_tool_content(data.get("content"))
            on_status("工具执行完成")
            on_tool_event({
                "kind": "tool_result",
                "callId": call_id,
                "name": name,
                "content": content_text,
                "isError": is_error,
            })

    approval_handler = _wrap_harness_approval(on_approval)
    session_id = f"amadeus-{uuid.uuid4().hex}"

    with DeepSeekHarness(config) as harness:
        with _active_lock:
            _active_session_id = session_id
            _active_harness = harness
        try:
            result: RunResult = harness.run(
                input=[{"type": "text", "text": system + "\n\n" + input_text}],
                session_id=session_id,
                on_notification=handle_notification,
                on_approval=approval_handler,
            )
        finally:
            with _active_lock:
                _active_session_id = None
                _active_harness = None
        if result.final_response:
            return result.final_response
        return ""


def is_harness_available() -> bool:
    """检查 DeepSeek Harness SDK 是否可用。"""
    return _HARNESS_AVAILABLE


def get_harness_import_error() -> str | None:
    """获取 SDK 导入失败原因。"""
    return _HARNESS_IMPORT_ERROR
