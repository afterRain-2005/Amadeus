"""CompanionController：聚合传感器+评估器+调度器。

由 desktop_pet.py 闭包内的 CompanionController 实例化（参考 AgentTask 模式）。
信号变化时调 handle_signal(snapshot, local_hour)，命中则 record_greeting + route_and_send。

接入表达层：handle_signal 接受 on_delta/on_status 回调，透传给 route_and_send，
让 companion 回复流式 delta 复用 desktop_pet 的 _agent_delta/_show_status。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from core.companion.evaluator import Evaluator
from core.companion.prompts import KURISU_PROACTIVE_PASS_THROUGH
from core.companion.scheduler import Scheduler
from core.companion.sensors import ContextSnapshot
from core.companion import storage
from core.storage import APP_DIR


class CompanionController:
    def __init__(self, *, config: dict, llm_config: dict) -> None:
        # 启动即建表：lightweight_memory 表此前只在设置页「清空记忆」里建，
        # 实际使用时 _companion_tick 首次查询就抛 OperationalError 被吞掉，
        # 导致 handle_signal 永不执行（无主动气泡）。此处兗底建表，失败静默降级。
        try:
            storage.init_schema()
        except Exception:
            pass  # companion 永不影响主流程
        self.scheduler = Scheduler(config)
        self.evaluator = Evaluator()
        self.llm_endpoint = llm_config.get("endpoint", "")
        self.llm_api_key = llm_config.get("api_key", "")
        self.llm_model = llm_config.get("model", "")
        self._last_user_msg_ts: Optional[float] = None
        # C-04 缓存：config 和 soul_md 在运行期几乎不变，避免每次 _speak 都读磁盘
        self._cached_config: Optional[dict] = None
        self._cached_soul_md: Optional[str] = None

    def on_user_message(self) -> None:
        """用户发消息时调用，更新冷却时间戳。"""
        self._last_user_msg_ts = time.time()

    def handle_signal(
        self, snapshot: ContextSnapshot, *, local_hour: float,
        on_delta=None, on_status=None, on_finished=None, on_expression=None,
    ) -> None:
        """传感器信号变化时调用。命中则触发问候。

        on_delta/on_status 回调透传给 route_and_send，让 desktop_pet 的
        表达层（_agent_delta/_show_status）接收 companion 回复的流式 delta。

        检查顺序遵循 scheduler.py 文档注释：
        1. 用户对话冷却（不读 storage）
        2. enabled/静音/概率/每日上限（should_consider，读 greeting_count_today）
        3. 全局冷却（读 last_greeting_ts）

        所有 storage 调用包 try/except：companion 永不影响主流程
        （DB 缺表/损坏时降级为 None / 0，等价于"无冷却记录/今日0次"）。
        """
        # 用户对话冷却
        if not self.scheduler.user_dialogue_cooldown_allows(
            last_user_msg_ts=self._last_user_msg_ts
        ):
            return
        # 静音/概率/上限（先做廉价门控，避免 storage 异常时仍走 LLM）
        try:
            count_today = storage.greeting_count_today()
        except Exception:
            count_today = 0
        if not self.scheduler.should_consider(
            local_hour=local_hour,
            idle_state=snapshot.idle_state,
            idle_seconds=snapshot.idle_seconds,
            greeting_count_today=count_today,
        ):
            return
        # 全局冷却
        try:
            last_ts_str = storage.last_greeting_ts()
        except Exception:
            last_ts_str = None
        last_ts_epoch = self._parse_iso_to_epoch(last_ts_str) if last_ts_str else None
        if not self.scheduler.global_cooldown_allows(last_greeting_ts_epoch=last_ts_epoch):
            return
        # 评估
        decision = self.evaluator.evaluate(
            snapshot, allow_llm=True, signal_type=snapshot.idle_state or "default",
            llm_endpoint=self.llm_endpoint, llm_api_key=self.llm_api_key,
            llm_model=self.llm_model,
        )
        if decision is None:
            return
        # 触发问候
        self._speak(
            decision,
            on_delta=on_delta,
            on_status=on_status,
            on_finished=on_finished,
            on_expression=on_expression,
        )

    def _speak(self, decision, *, on_delta=None, on_status=None, on_finished=None, on_expression=None) -> None:
        """写入 storage + 调 route_and_send。

        on_delta/on_status 回调透传给 route_and_send，由 desktop_pet 注入实际
        表达层回调（_agent_delta/_show_status）；未注入时走 no-op，不影响主流程。

        C-03 修复：先调 route_and_send，成功后才 record_greeting，
        避免 LLM 失败时 storage 记录“从未说出口的问候”导致每日上限虚高。
        """
        # 延迟导入避免循环依赖
        from core.backend_router import route_and_send
        inject = KURISU_PROACTIVE_PASS_THROUGH.format(text=decision.text)
        try:
            reply, _backend = route_and_send(
                config=self._get_config(),
                input_text=decision.text,
                soul_md=self._get_soul_md(),
                conversation_history=None,
                memories=None,
                on_delta=on_delta or (lambda t: None),
                on_status=on_status or (lambda t: None),
                system_role="companion",
                skip_history=True,
                inject_system_prompt=inject,
            )
            # 成功后才记录问候（C-03）
            try:
                storage.record_greeting(decision.text, decision.topic, decision.emotion)
            except Exception:
                pass  # storage 失败不影响回复
            if on_expression is not None:
                try:
                    from core.companion.expression import decide_expression
                    from core.storage import load_config
                    cfg = load_config()
                    ollama = (cfg.get("agent_router") or {}).get("ollama")
                    expr = decide_expression(reply, ollama=ollama)
                    on_expression(expr)
                except Exception:
                    pass  # 表现层永不影响主流程
            if on_finished is not None:
                on_finished(reply)
        except Exception:
            pass  # companion 永不影响主流程

    @staticmethod
    def _parse_iso_to_epoch(iso_str: str) -> float:
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return dt.timestamp()
        except (ValueError, TypeError, AttributeError):
            return 0.0

    def _get_config(self) -> dict:
        """获取配置（缓存，避免每次 _speak 都读磁盘）。"""
        if self._cached_config is None:
            from core.storage import load_config
            self._cached_config = load_config()
        return self._cached_config

    def _get_soul_md(self) -> str:
        """读取 SOUL.md（缓存，失败回退 KURISU_PERSONALITY）。"""
        if self._cached_soul_md is None:
            from core.storage import APP_DIR
            soul_path = APP_DIR / "SOUL.md"
            if soul_path.exists():
                self._cached_soul_md = soul_path.read_text(encoding="utf-8")
            else:
                from config import get_character_by_id
                c = get_character_by_id("kurisu")
                self._cached_soul_md = c.personality if c else ""
        return self._cached_soul_md

    def invalidate_cache(self) -> None:
        """清除缓存（设置页保存后调用）。"""
        self._cached_config = None
        self._cached_soul_md = None

    def start(self, parent=None) -> None:
        """启动所有传感器 QTimer。

        实际 QTimer 绑定由 desktop_pet.py 闭包内完成（参考 AgentTask 模式），
        此接口保留给未来 P1 阶段做"Controller 自管传感器"的演进。
        """
        pass

    def stop(self) -> None:
        """停止所有传感器。"""
        pass
