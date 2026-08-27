"""CRT 终端 HTML 构建纯函数（从 desktop_pet.py 提出）。

fauux 令牌：rose #d2738a / cream #c1b492 / b=rose。
本模块顶层不得 import PySide6（同 ui/bubble.py 约定）。
"""
from __future__ import annotations

import html
import os
from pathlib import Path

from ui.theme import CREAM, DIM, FONT_MONO, ROSE, TERMINAL_PROMPT


# ==== CRT 终端 HTML 构建（令牌统一来自 ui.theme）====

# 兼容别名：老代码从本模块导入 _TERMINAL_*
_TERMINAL_ROSE = ROSE
_TERMINAL_CREAM = CREAM
_TERMINAL_DIM = DIM
_TERMINAL_PROMPT = TERMINAL_PROMPT


# ============================================================
# 函数：_render_markdown()
# 作用：把 LLM 回复的 markdown 文本渲染成 HTML（支持代码块/表格等），
#       用于终端显示。markdown 库未安装时退化为纯文本（HTML 转义）。
#       单段落时去掉 <p> 包装，让内容与 "kurisu>" 前缀保持同行。
# 参数：
#   text str markdown 源文本
# 返回值：str —— 渲染后的 HTML
# ============================================================
def _render_markdown(text: str) -> str:
    """把 LLM 回复渲染成 HTML（markdown 支持）。markdown 库缺失时退化为纯文本。"""
    safe_text = html.escape(text)
    try:
        import markdown
        rendered = markdown.markdown(
            safe_text,
            extensions=["fenced_code", "tables", "nl2br"],
            output_format="html5",
        )
    except ImportError:
        return safe_text.replace("\n", "<br>")
    # 单段落时去掉 <p> 包装，保持与 kurisu> 前缀同行
    if rendered.startswith("<p>") and rendered.endswith("</p>") and rendered.count("<p>") == 1:
        rendered = rendered[3:-4]
    return rendered


# ============================================================
# 函数：_build_terminal_line_html()
# 作用：把终端的一行日志渲染成 HTML。按 kind 分类显示：
#       cmd=用户命令 / out=AI 输出（支持 markdown）/ err=错误（玫瑰色!）/
#       tool=工具调用（⟳ 图标）/ diff=文件差异 / 其他=普通行。
# 参数：
#   kind  str   行类型（cmd/out/err/sys/tool/diff）
#   text  str   行内容
#   extra dict|None 附加信息（diff 类型时含 path/old/new）
# 返回值：str —— 单行终端 HTML
# ============================================================
def _build_terminal_line_html(kind: str, text: str, extra: dict | None = None) -> str:
    """单行终端 HTML。kind: cmd/out/err/sys/tool=工具调用/diff=差异。"""
    safe = html.escape(text).replace("\n", "<br>")
    if kind == "cmd":
        return (
            f"<div style='margin:2px 0'><span style='color:{_TERMINAL_ROSE}'>"
            f"{_TERMINAL_PROMPT}</span> <span style='color:{_TERMINAL_CREAM}'>{safe}</span></div>"
        )
    if kind == "out":
        rendered = _render_markdown(text)
        # 块级 markdown（代码块/列表/标题/多段落）用 div 包裹，前缀独立一行；否则同行
        if any(tag in rendered for tag in ("<pre", "<ul", "<ol", "<h1", "<h2", "<h3", "<h4", "<h5", "<h6", "<blockquote", "<table", "<p>")):
            return (
                f"<div style='margin:2px 0'><span style='color:{_TERMINAL_ROSE}'>kurisu&gt;</span></div>"
                f"<div style='color:{_TERMINAL_CREAM}'>{rendered}</div>"
            )
        return (
            f"<div style='margin:2px 0'><span style='color:{_TERMINAL_ROSE}'>kurisu&gt;</span> "
            f"<span style='color:{_TERMINAL_CREAM}'>{rendered}</span></div>"
        )
    if kind == "err":
        return f"<div style='margin:2px 0;color:{_TERMINAL_ROSE}'>! {safe}</div>"
    if kind == "tool":
        return (
            f"<div style='margin:2px 0'><span style='color:{_TERMINAL_ROSE}'>⟳</span> "
            f"<span style='color:{_TERMINAL_DIM}'>{safe}</span></div>"
        )
    if kind == "result":
        return (
            f"<pre style='margin:3px 0 5px 16px;color:{CREAM};"
            f"white-space:pre-wrap;font-family:{FONT_MONO}'>"
            f"{html.escape(text)}</pre>"
        )
    if kind == "diff":
        extra = extra or {}
        return _render_diff_html(str(extra.get("path", "")), extra.get("old"), extra.get("new"))
    return f"<div style='margin:2px 0;color:{_TERMINAL_DIM}'>{safe}</div>"


# ============================================================
# 函数：_render_diff_html()
# 作用：把 str_replace_editor 工具的 old/new 文本渲染成 CRT 风格的行内
#       diff（+ 新增=米黄 / - 删除=玫瑰 / 空格=上下文=暗色），
#       difflib.ndiff 计算逐行差异。
# 参数：
#   path str       文件名（显示在 diff 头部）
#   old  str|None  修改前的文件内容（None=新建文件）
#   new  str|None  修改后的文件内容
# 返回值：str —— diff 的 HTML 片段
# ============================================================
def _render_diff_html(path: str, old: str | None, new: str | None) -> str:
    """把 str_replace_editor 的 old/new 渲染成 CRT 风格行内 diff（+ 新增 / - 删除 / 空格 上下文）。"""
    import difflib
    header = f"<div style='color:{_TERMINAL_DIM}'>diff — {html.escape(path) if path else '(untitled)'}</div>"
    if old is None:
        body = "".join(
            f"<div style='color:{_TERMINAL_CREAM}'>+ {html.escape(line)}</div>"
            for line in (new or "").splitlines()
        )
        if not body:
            body = f"<div style='color:{_TERMINAL_DIM}'>(empty file)</div>"
        return (
            f"<div style='margin:4px 0;border-left:2px solid {_TERMINAL_ROSE};padding-left:8px'>"
            f"{header}{body}</div>"
        )
    rows = []
    for line in difflib.ndiff((old or "").splitlines(), (new or "").splitlines()):
        tag = line[:2]
        content = line[2:]
        if tag == "- ":
            rows.append(f"<div style='color:{_TERMINAL_ROSE}'>- {html.escape(content)}</div>")
        elif tag == "+ ":
            rows.append(f"<div style='color:{_TERMINAL_CREAM}'>+ {html.escape(content)}</div>")
        elif tag == "? ":
            continue
        else:
            rows.append(f"<div style='color:{_TERMINAL_DIM}'>  {html.escape(content)}</div>")
    return (
        f"<div style='margin:4px 0;border-left:2px solid {_TERMINAL_ROSE};padding-left:8px'>"
        f"{header}{''.join(rows)}</div>"
    )


# ============================================================
# 函数：_editor_diff_extra()
# 作用：从 str_replace_editor 工具的 arguments 里提取用于显示 diff 的
#       path/old/new 三元组。按 command 类型处理：
#       create=新建文件（old 为 None）；str_replace=替换（old/new 都有）。
# 参数：
#   args dict 工具的 arguments（含 command/path/old_str/new_str/file_text 等）
# 返回值：dict —— {"path": str, "old": str|None, "new": str|None}
# ============================================================
def _editor_diff_extra(args: dict) -> dict:
    """从 str_replace_editor 的 arguments 提取 diff 的 path/old/new。"""
    command = str(args.get("command", ""))
    path = str(args.get("path", ""))
    if command == "create":
        return {"path": path, "old": None, "new": args.get("file_text", "")}
    if command == "str_replace":
        return {"path": path, "old": args.get("old_str"), "new": args.get("new_str")}
    if command == "insert":
        line = args.get("insert_line")
        label = f"{path}:{line}" if line is not None else path
        return {"path": label, "old": "", "new": args.get("new_str", "")}
    return {"path": path, "old": None, "new": None}


# ============================================================
# 函数：_tool_args_summary()
# 作用：把工具调用的 arguments 压缩成一行简短摘要（最多取前 3 个参数、
#       每个值超过 40 字符截断加 …），用于终端工具行显示。
# 参数：
#   args dict 工具参数
# 返回值：str —— 摘要文本（空参数时返回空字符串）
# ============================================================
def _tool_args_summary(args: dict) -> str:
    if not args:
        return ""
    parts = []
    for key, value in list(args.items())[:3]:
        s = str(value)
        if len(s) > 40:
            s = s[:40] + "…"
        parts.append(f"{key}={s}")
    return " ".join(parts)


def _terminal_token_start(text: str) -> int:
    """返回终端输入中最后一个未闭合参数的起始位置。"""
    token_start = 0
    quote = ""
    for index, character in enumerate(text):
        if quote:
            if character == quote:
                quote = ""
        elif character in ("'", '"'):
            quote = character
        elif character.isspace():
            token_start = index + 1
    return token_start


def _complete_terminal_input(
    text: str,
    history: list[str],
    cwd: str | os.PathLike[str] | None = None,
) -> str | None:
    """按整行历史优先、当前参数文件路径次之补全终端输入。"""
    if not text:
        return None
    history_matches = [item for item in reversed(history) if item.startswith(text) and item != text]
    if history_matches:
        common = os.path.commonprefix(history_matches)
        if len(common) > len(text):
            return common

    token_start = _terminal_token_start(text)
    token = text[token_start:]
    if not token:
        return None
    quote = token[0] if token[0] in ("'", '"') else ""
    raw_token = token[1:] if quote else token
    expanded = os.path.expanduser(raw_token)
    base = Path(cwd) if cwd is not None else Path.cwd()
    candidate_path = Path(expanded)
    search_path = candidate_path if candidate_path.is_absolute() else base / candidate_path

    import glob
    matches = glob.glob(str(search_path) + "*")
    if not matches:
        return None
    displays: list[str] = []
    for match in sorted(matches, key=str.casefold):
        matched_path = Path(match)
        if candidate_path.is_absolute():
            display = str(matched_path)
        else:
            display = os.path.relpath(matched_path, base)
        if matched_path.is_dir():
            display += os.sep
        displays.append(quote + display)
    completed_token = os.path.commonprefix(displays)
    if len(completed_token) <= len(token):
        return None
    return text[:token_start] + completed_token


# ============================================================
# 函数：_line_cache_key()
# 作用：计算终端行的缓存键。含 extra(dict) 的行用 id() 作为键的一部分
#       （dict 不可哈希，且避免重复序列化的开销）。
# 参数：
#   item tuple —— 终端行 (kind, text) 或 (kind, text, extra)
# 返回值：tuple —— 缓存键
# ============================================================
def _line_cache_key(item) -> tuple:
    """终端行的缓存键。extra(dict) 用 id() 避免不可哈希与重复序列化开销。"""
    if len(item) == 3:
        return (item[0], item[1], id(item[2]))
    return (item[0], item[1])


# ============================================================
# 函数：_build_terminal_html()
# 作用：把终端的所有行（lines）拼成完整 HTML（含标题栏"amadeus shell"
#       和分隔线），供 QTextBrowser 显示（自动滚动到底部）。
# 参数：
#   lines list —— 终端行列表，每项为 (kind, text) 或 (kind, text, extra)
# 返回值：str —— 完整终端 HTML
# ============================================================
def _build_terminal_html(lines: list) -> str:
    """终端完整 HTML（QTextBrowser 用，自动滚底）。"""
    parts = [
        f"<div style='color:{_TERMINAL_DIM};font-size:9px'>║▒░ amadeus shell — wired session</div>",
        f"<div style='border-top:1px solid {_TERMINAL_ROSE};margin:2px 0 6px 0'></div>",
    ]
    for item in lines:
        if len(item) == 3:
            kind, text, extra = item
            parts.append(_build_terminal_line_html(kind, text, extra))
        else:
            kind, text = item
            parts.append(_build_terminal_line_html(kind, text))
    return "".join(parts)
