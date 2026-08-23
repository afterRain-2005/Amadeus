"""后台 AI 请求任务：AgentSignals + AgentTask（从 desktop_pet.run_overlay 提出）。

AgentTask 在 QThreadPool 里调用 route_and_send，结果经 Qt 信号回主线程。
character 为可选注入（缺省时惰性取 get_character_by_id("kurisu")，行为不变）。
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRunnable, Signal

from config import get_character_by_id
from core.llm.agent_client import _load_soul_md
from core.storage import load_config


# ============================================================
# 类：AgentSignals（QObject）
# 作用：后台 AI 任务（AgentTask）与主线程之间的信号桥。
#       Qt 信号可以在线程之间安全传递：AI 任务在后台线程发出信号，
#       主线程通过 connect 接收并更新 UI。
#       信号列表：status=状态文本 / delta=流式增量 / finished=完整回复 /
#       failed=失败信息 / tool_event=工具调用事件 / confirmation=工具审批请求
# ============================================================
class AgentSignals(QObject):
    status = Signal(str)
    delta = Signal(str)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal(str)
    tool_event = Signal(object)
    confirmation = Signal(object)

# ============================================================
# 类：AgentTask（QRunnable）
# 作用：★后台 AI 请求任务。把"调用 AI + 工具执行"放到线程池里跑，
#       避免阻塞 Qt 主线程（否则界面会卡死）。
#       流程：run() 里调 route_and_send（路由+发消息），结果通过
#       signals 发回主线程（流式增量发 delta，完整结果发 finished）。
# ============================================================
class AgentTask(QRunnable):
    # ============================================================
    # 函数：__init__()
    # 作用：初始化任务，保存对话历史和记忆
    # 参数：
    #   history  list[dict] 对话历史（最后 14 条）
    #   memories list|None  长期记忆
    # 返回值：无（None）
    # ============================================================
    def __init__(
        self,
        history,
        memories=None,
        route_override: str | None = None,
        response_max_tokens: int | None = 700,
        inject_system_prompt: str | None = None,
        terminal_cwd: str | None = None,
        terminal_session_id: str | None = None,
        character=None,
    ) -> None:
        super().__init__()
        self._character = character
        self.history = history
        self.memories = memories or []
        self.route_override = route_override
        self.response_max_tokens = response_max_tokens
        self.inject_system_prompt = inject_system_prompt
        self.terminal_cwd = terminal_cwd
        self.terminal_session_id = terminal_session_id
        self.cancel_event = threading.Event()
        self.signals = AgentSignals()

    def cancel(self) -> None:
        self.cancel_event.set()

    # ============================================================
    # 函数：run()
    # 作用：★任务的线程入口（QThreadPool 调用）。
    #       读取 SOUL.md（或人设回退），调用 route_and_send 发消息，
    #       期间所有状态通过 self.signals 信号发出。
    #       成功→finished(reply)；任何异常→failed(错误信息)。
    # 参数：无
    # 返回值：无（None）
    # ============================================================
    def run(self) -> None:
        config = dict(load_config())
        if self.route_override:
            router = dict(config.get("agent_router") or {})
            if self.route_override == "local":
                router["mode"] = "chat"
                router["auto_route"] = False
            elif self.route_override == "terminal_auto":
                router["mode"] = "chat"
                router["auto_route"] = True
                router["auto_targets"] = list(router.get("auto_targets") or ["local", "harness"])
            else:
                router["mode"] = self.route_override
                router["auto_route"] = False
            config["agent_router"] = router
        if self.terminal_cwd:
            harness_cfg = dict(config.get("harness") or {})
            harness_cfg["cwd"] = self.terminal_cwd
            config["harness"] = harness_cfg
        # 读取 SOUL.md（若存在），否则回退到 config.py 中的 KURISU_PERSONALITY
        soul_md = _load_soul_md("kurisu") or (
        self._character.personality if self._character is not None
        else get_character_by_id("kurisu").personality
    )
        # 聊天屏幕感知：开启时附加当前屏幕一句话描述（缓存内复用，失败静默）
        try:
            from core.vision.screen_context import build_screen_prompt
            screen_prompt = build_screen_prompt(config)
            if screen_prompt:
                self.inject_system_prompt = (
                    f"{self.inject_system_prompt}\n\n{screen_prompt}"
                    if self.inject_system_prompt else screen_prompt
                )
        except Exception:
            pass
        try:
            from core.llm.backend_router import route_and_send
            reply, _backend = route_and_send(
                config=config,
                input_text=self.history[-1]["content"],
                soul_md=soul_md,
                conversation_history=self.history[:-1],
                memories=self.memories,
                on_status=self.signals.status.emit,
                on_delta=self.signals.delta.emit,
                on_tool_event=self.signals.tool_event.emit,
                on_approval=self._handle_approval,
                inject_system_prompt=self.inject_system_prompt,
                response_max_tokens=self.response_max_tokens,
                cancel_event=self.cancel_event,
                harness_session_id=self.terminal_session_id,
            )
            if self.cancel_event.is_set():
                self.signals.cancelled.emit(reply)
            else:
                self.signals.finished.emit(reply)
        except Exception as exc:
            if self.cancel_event.is_set():
                self.signals.cancelled.emit("")
            else:
                self.signals.failed.emit(str(exc))

    # ============================================================
    # 函数：_handle_approval()
    # 作用：★工具审批（危险操作需要用户确认）。
    #       AI 想执行高权限工具时，发送 confirmation 信号到主线程，
    #       主线程弹出确认框；本函数用 Event 阻塞等待用户选择
    #       （once/session/always/deny），然后返回选择结果。
    # 参数：
    #   payload dict 工具调用详情（command/description 等）
    # 返回值：str —— 用户选择："once" | "session" | "always" | "deny"
    # ============================================================
    def _handle_approval(self, payload: dict) -> str:
        import threading
        request = {"payload": payload, "event": threading.Event(), "choice": "deny"}
        self.signals.confirmation.emit(request)
        while not request["event"].wait(0.1):
            if self.cancel_event.is_set():
                return "deny"
        return request["choice"]
