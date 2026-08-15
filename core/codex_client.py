"""codex 模式客户端：codex exec 子进程 + JSONL 事件适配 + AGENTS.md 人设。

设计依据 docs/superpowers/specs/2026-08-15-agent-mode-design.md §4.3：
- 首轮 `codex exec ... "<input>"`；后续加 `resume --last` 延续会话
- 角色一致性 = workspace/AGENTS.md（SOUL.md + KURISU_OUTPUT_FORMAT 生成）
- 最终回复以 -o 产物文件为真相兜底；JSONL 仅驱动 on_delta/on_status
- agent_message 事件是全量快照 → 增量转换（保持 on_delta 追加语义）
- 默认 read-only 沙箱；超时 terminate()
popen 参数为依赖注入，供测试 mock。
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading

AGENTS_MD_HEADER = """# Kurisu（牧瀬紅莉栖）— 桌宠人格指令

> 本文件由 amadeus-py 自动生成（codex 模式人设）。修改会被覆盖，请改 SOUL.md 源。

"""


def ensure_agents_md(workspace: Path, soul_md: str, output_format: str) -> Path:
    """workspace/AGENTS.md 不存在时写入（人设 + 输出格式）。返回其路径。"""
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "AGENTS.md"
    if not path.exists():
        path.write_text(
            AGENTS_MD_HEADER + soul_md.strip() + "\n\n---\n\n" + output_format.strip() + "\n",
            encoding="utf-8")
    return path


def parse_event_line(line: str) -> tuple[str, str] | None:
    """宽容解析一行 codex --json 事件：返回 (kind, text) 或 None。

    kind: "delta"（agent 消息文本）| "status"（工具/命令进展）。
    未识别结构一律返回 None（隔离 codex 版本间事件格式差异）。
    """
    line = line.strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    item = event.get("item") or event.get("msg") or {}
    if not isinstance(item, dict):
        return None
    item_type = str(item.get("type") or event.get("type") or "")
    if item_type == "agent_message":
        text = str(item.get("text") or "")
        return ("delta", text) if text else None
    if item_type in ("command_execution", "file_change", "mcp_tool_call",
                     "todo_list", "web_search", "view_image"):
        detail = item.get("command") or item.get("changes") or item.get("tool") \
            or item.get("status") or ""
        return ("status", f"codex: {item_type} {detail}".strip())
    if item_type in ("task_started", "task_complete", "session_configured", "turn_context"):
        return ("status", f"codex: {item_type}")
    return None


def run_codex_turn(
    *,
    input_text: str,
    workspace: Path,
    resume: bool = False,
    sandbox: str = "read-only",
    timeout: float = 120.0,
    on_delta=lambda text: None,
    on_status=lambda text: None,
    popen=subprocess.Popen,
) -> str:
    """跑一轮 codex exec，返回最终回复文本。失败抛 RuntimeError。"""
    workspace = Path(workspace)
    out_file = workspace / "codex_last.txt"
    argv = [
        "codex", "exec", "--json", "--skip-git-repo-check",
        "-s", sandbox, "-C", str(workspace), "-o", str(out_file),
    ]
    if resume:
        argv += ["resume", "--last"]
    argv.append(input_text)

    proc = popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                 text=True, encoding="utf-8", errors="replace", cwd=str(workspace))
    last_text = ""
    emitted = ""

    def _read() -> None:
        nonlocal last_text, emitted
        assert proc.stdout is not None
        for raw in proc.stdout:
            parsed = parse_event_line(raw)
            if not parsed:
                continue
            kind, text = parsed
            if kind == "delta":
                last_text = text
                if text.startswith(emitted) and len(text) > len(emitted):
                    on_delta(text[len(emitted):])   # 全量快照 → 增量
                    emitted = text
                elif not text.startswith(emitted):
                    on_delta("\n" + text)          # 罕见：快照被改写，整段补发
                    emitted = text
            else:
                on_status(text)

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        raise RuntimeError(f"codex 执行超时（{timeout:.0f}s）")
    reader.join(timeout=5)
    if proc.returncode != 0:
        raise RuntimeError(f"codex exec 退出码 {proc.returncode}")
    if out_file.exists():
        content = out_file.read_text(encoding="utf-8", errors="replace").strip()
        if content:
            return content                        # -o 产物文件是真相兜底
    if last_text:
        return last_text
    raise RuntimeError("codex 未产生任何回复文本")
