# tests/test_agent_terminal.py
"""CRT 终端 HTML 构建器单元测试（fauux 令牌：rose #d2738a / cream #c1b492）。"""
from __future__ import annotations


def test_cmd_line_rose_prompt_cream_text():
    from desktop_pet import _build_terminal_line_html
    html = _build_terminal_line_html("cmd", "你好")
    assert "guest@wired:~$" in html
    assert "#d2738a" in html   # 提示符 rose
    assert "#c1b492" in html   # 正文 cream
    assert "你好" in html


def test_out_line_kurisu_prefix():
    from desktop_pet import _build_terminal_line_html
    html = _build_terminal_line_html("out", "记忆消除是不可能的")
    assert "kurisu&gt;" in html
    assert "#c1b492" in html
    assert "记忆消除是不可能的" in html


def test_err_line_rose_bang():
    from desktop_pet import _build_terminal_line_html
    html = _build_terminal_line_html("err", "连接失败")
    assert "! 连接失败" in html
    assert "#d2738a" in html


def test_sys_line_dim():
    from desktop_pet import _build_terminal_line_html
    html = _build_terminal_line_html("sys", "session restored")
    assert "#8a7f63" in html


def test_html_escaped():
    from desktop_pet import _build_terminal_line_html
    html = _build_terminal_line_html("cmd", "<script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_newline_becomes_br():
    from desktop_pet import _build_terminal_line_html
    html = _build_terminal_line_html("out", "第一行\n第二行")
    assert "<br>" in html


def test_full_html_has_fauux_header():
    from desktop_pet import _build_terminal_html
    html = _build_terminal_html([("cmd", "hi")])
    assert "║▒░" in html                      # fauux 分隔符装饰
    assert "border-top:1px solid #d2738a" in html  # hr rose
    assert "guest@wired:~$" in html


def test_assistant_markdown_escapes_raw_html():
    from desktop_pet import _build_terminal_line_html

    html = _build_terminal_line_html("out", "<script>alert(1)</script> **安全**")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_tool_result_has_no_assistant_prefix():
    from desktop_pet import _build_terminal_line_html

    html = _build_terminal_line_html("result", "第一行\n第二行")
    assert "kurisu&gt;" not in html
    assert "<pre" in html
    assert "第一行\n第二行" in html


def test_insert_editor_diff_is_rendered_as_addition():
    from desktop_pet import _build_terminal_line_html, _editor_diff_extra

    extra = _editor_diff_extra({
        "command": "insert",
        "path": "demo.py",
        "insert_line": 3,
        "new_str": "print('ok')",
    })
    html = _build_terminal_line_html("diff", "", extra)
    assert "demo.py:3" in html
    assert "+ print(&#x27;ok&#x27;)" in html


def test_completion_prefers_command_history():
    from desktop_pet import _complete_terminal_input

    assert _complete_terminal_input("git st", ["git status", "git stash"]) == "git sta"


def test_completion_completes_quoted_path_argument(tmp_path):
    from desktop_pet import _complete_terminal_input

    folder = tmp_path / "folder with space"
    folder.mkdir()
    (folder / "alpha.txt").write_text("ok", encoding="utf-8")

    completed = _complete_terminal_input('type "folder with space\\al', [], cwd=tmp_path)
    assert completed == 'type "folder with space\\alpha.txt'
