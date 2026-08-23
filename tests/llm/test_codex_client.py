# tests/test_codex_client.py
"""codex_client：事件解析/追加语义/超时/退出码（FakePopen，不打真 codex）。

JSONL 事件 fixture 为宽容契约样本（真实 codex 输出可能多事件/多字段，
parse_event_line 对未知结构返回 None 才是契约核心）。
"""
import subprocess
import time

import pytest

from core.llm.codex_client import build_codex_input, ensure_agents_md, parse_event_line, run_codex_turn


class FakeProc:
    """stdout 为生成器：耗尽时置 returncode；hang=True 永不结束（测超时）。"""

    def __init__(self, lines, returncode=0, hang=False):
        self._lines = list(lines)
        self._rc = returncode
        self._hang = hang
        self.stdout = self._gen()
        self.returncode = None
        self.terminated = False

    def _gen(self):
        for ln in self._lines:
            yield ln
        if not self._hang:
            self.returncode = self._rc

    def wait(self, timeout=None):
        deadline = time.monotonic() + (timeout if timeout else 3600)
        while self.returncode is None:
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired("codex", timeout)
            time.sleep(0.001)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -9


EVENTS = [
    '{"id":"0","msg":{"type":"session_configured","session_id":"s1"}}',
    '{"type":"item.started","item":{"type":"agent_message"}}',
    '{"type":"item.completed","item":{"type":"agent_message","text":"你好"}}',
    '{"type":"item.completed","item":{"type":"command_execution","command":"dir","status":"done"}}',
    '{"type":"item.completed","item":{"type":"agent_message","text":"你好，我是红莉栖"}}',
    'not-json-noise',
]


def test_parse_event_line_delta():
    assert parse_event_line(EVENTS[2]) == ("delta", "你好")


def test_parse_event_line_status():
    kind, text = parse_event_line(EVENTS[3])
    assert kind == "status" and "command_execution" in text


def test_parse_event_line_noise():
    assert parse_event_line(EVENTS[5]) is None
    assert parse_event_line("") is None
    assert parse_event_line('{"type":"unknown_thing"}') is None


def test_ensure_agents_md(tmp_path):
    ws = tmp_path / "ws"
    path = ensure_agents_md(ws, "人设A", "格式B")
    assert path.exists()
    assert "人设A" in path.read_text(encoding="utf-8")
    path.write_text("KEEP", encoding="utf-8")  # 已存在不覆盖
    ensure_agents_md(ws, "人设A", "格式B")
    assert path.read_text(encoding="utf-8") == "KEEP"


def test_build_codex_input_includes_memory_and_history():
    text = build_codex_input(
        "问题",
        memories=[{"content": "我喜欢叉子"}, {"content": "我叫阿尔法"}],
        conversation_history=[{"role": "user", "content": "上一句"}, {"role": "assistant", "content": "上一个回复"}],
    )
    assert "桌宠本地记忆" in text
    assert "我喜欢叉子" in text
    assert "最近对话上下文" in text
    assert "用户: 上一句" in text
    assert "红莉栖: 上一个回复" in text
    assert text.endswith("【本轮用户输入】\n问题")


def test_run_codex_turn_append_semantics(tmp_path):
    deltas, statuses = [], []
    reply = run_codex_turn(
        input_text="hi", workspace=tmp_path, popen=lambda a, **k: FakeProc(EVENTS),
        on_delta=deltas.append, on_status=statuses.append)
    assert deltas == ["你好", "，我是红莉栖"]  # 全量快照 → 增量（追加语义）
    assert any("command_execution" in s for s in statuses)
    assert reply == "你好，我是红莉栖"          # 无 -o 文件时回退最后 delta


def test_run_codex_turn_output_file_truth(tmp_path):
    def popen(argv, **kw):
        (tmp_path / "codex_last.txt").write_text("最终答案", encoding="utf-8")
        return FakeProc(EVENTS)

    reply = run_codex_turn(
        input_text="hi", workspace=tmp_path, popen=popen)
    assert reply == "最终答案"                  # -o 产物文件是真相兜底


def test_run_codex_turn_timeout(tmp_path):
    proc = FakeProc(EVENTS, hang=True)
    with pytest.raises(RuntimeError, match="超时"):
        run_codex_turn(input_text="hi", workspace=tmp_path, timeout=0.2,
                       popen=lambda a, **k: proc)
    assert proc.terminated is True


def test_run_codex_turn_nonzero(tmp_path):
    with pytest.raises(RuntimeError, match="退出码"):
        run_codex_turn(input_text="hi", workspace=tmp_path,
                       popen=lambda a, **k: FakeProc(EVENTS, returncode=1))


def test_run_codex_turn_argv(tmp_path):
    calls = {}

    def popen(argv, **kw):
        calls["argv"] = argv
        return FakeProc(EVENTS)

    run_codex_turn(input_text="问题", workspace=tmp_path, resume=True, popen=popen)
    argv = calls["argv"]
    assert argv[0] == "codex" and "exec" in argv and "--json" in argv
    assert "resume" in argv and "--last" in argv
    assert argv[-1] == "问题"
    assert argv[argv.index("-s") + 1] == "read-only"
    assert "-C" in argv and "-o" in argv
