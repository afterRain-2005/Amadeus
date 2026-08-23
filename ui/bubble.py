"""气泡文本/布局纯函数（从 desktop_pet.py 提出，便于单测与复用）。

约定：本模块顶层不得 import PySide6（desktop_pet 主进程/renderer 子进程
都可能在无 Qt 环境导入本模块）；Qt 依赖一律函数内延迟导入。
"""
from __future__ import annotations

import html


# ============================================================
# 函数：_sync_bubble_accessories()
# 作用：让气泡顶部的名牌（header）和底部的注脚（footer）跟随气泡的
#       几何位置自动对齐。
#       初始化早期 header/footer 可能还没创建（在 reply_bubble 之后才建），
#       所以先用 None 判断做保护，避免 AttributeError 崩溃。
# 参数：
#   header  QWidget|None 顶部名牌控件
#   footer  QWidget|None 底部注脚控件
#   x       int 气泡横坐标
#   y       int 气泡纵坐标（Dock 栏上方）
#   w       int 气泡宽度
#   h       int 气泡高度
# 返回值：无（None）
# ============================================================
def _sync_bubble_accessories(header, footer, corners, status_line, x, y, w, h) -> None:
    """气泡头部名牌/底部注脚/四角括号/状态行跟随气泡几何（fauux 稿⑤⑩④）。

    初始化早期（bubble_header/footer 创建于 reply_bubble 之后）可能先行调用
    _set_bubble_text，此时配件尚未创建，保护式访问避免 AttributeError
    （历史会话存在时 exe/start.bat 启动即静默崩溃）。
    v4：名牌/注脚改为一体标签（贴合气泡上下缘，不再裸叠立绘），
        corners 为 4 个 ⌈⌉⌊⌋ 角括号 QLabel，status_line 为状态行。
    """
    if header is not None:
        hw = min(w, 150)
        header.setGeometry(x + (w - hw) // 2, y - 2, hw, 16)
    if footer is not None:
        fw = min(w, 200)
        footer.setGeometry(x + (w - fw) // 2, y + h - 16, fw, 16)
    if corners is not None:
        c = 12
        corners[0].setGeometry(x - 5, y - 2, c, c)
        corners[1].setGeometry(x + w - 7, y - 2, c, c)
        corners[2].setGeometry(x - 5, y + h - 7, c, c)
        corners[3].setGeometry(x + w - 7, y + h - 7, c, c)
    if status_line is not None:
        status_line.setGeometry(x + 8, y + h + 6, w - 16, 14)


# ============================================================
# 函数：_wrap_bubble_html()
# 作用：把气泡正文包装为富文本 HTML：HTML 转义 + 换行转 <br> +
#       1.5 行距 + 左对齐（QLabel 富文本才能表达 line-height，
#       QSS 不支持 QLabel 行距）。纯函数，便于单元测试。
# 参数：
#   text str 气泡纯文本
# 返回值：str —— 富文本 HTML
# ============================================================
def _wrap_bubble_html(text: str) -> str:
    """气泡正文富文本包装（v4：1.5 行距 + 左对齐）。"""
    safe = html.escape(text).replace("\n", "<br>")
    return (
        "<html><body style='margin:0;line-height:150%;text-align:left;"
        "color:#c1b492;font-size:14px'>" + safe + "</body></html>"
    )


# ============================================================
# 函数：_bubble_size_hint()
# 作用：用 QTextDocument 估算气泡富文本尺寸（QLabel 无法用
#       QFontMetrics 测富文本行距）。纯函数，便于单元测试。
# 参数：
#   html   str     富文本 HTML（_wrap_bubble_html 输出）
#   font   QFont   气泡字体（QTextDocument 默认字体）
#   max_w  int     最大宽度
# 返回值：tuple[int, int] —— (宽, 高)，含 padding 余量
# ============================================================
def _bubble_size_hint(html_text: str, font, max_w: int) -> tuple[int, int]:
    """QTextDocument 估算富文本气泡尺寸（v4）。"""
    from PySide6.QtGui import QTextDocument

    doc = QTextDocument()
    doc.setDefaultFont(font)
    doc.setHtml(html_text)
    doc.setTextWidth(max_w - 36)
    return int(doc.idealWidth()) + 36, int(doc.size().height()) + 24


# ============================================================
# 函数：_streamed_display_text()
# 作用：从流式累积文本中提取"当前可上屏"的气泡文字（流式气泡体感提速）。
#       规则：去掉开头 [emotion:xxx] 标签（含只到达一半的残缺标签），
#       只取第一个 === 分隔符之前的内容（残缺的 == 也按分隔符处理），
#       保证显示文本随 delta 到达单调增长，情绪标签/日语段不闪现在气泡里。
#       ★纯函数（不碰 UI、无副作用），方便单元测试。
# 参数：
#   streamed str 流式累积的全部文本
# 返回值：str —— 当前应显示的文本（空串表示尚无可显示内容）
# ============================================================
def _streamed_display_text(streamed: str) -> str:
    """流式阶段的气泡显示文本（纯函数，便于单元测试）。"""
    import re

    text = streamed.lstrip()
    # 情绪标签可能只到达一半（如 "[emotion:smi"），一并容忍去掉
    text = re.sub(r"^\[emotion:[^\]]*\]?", "", text)
    # 孤立的 "[" 片段（可能是 [emotion: 的第一片）不上屏，等标签闭合或正文到达
    if text.startswith("[") and "]" not in text:
        return ""
    # 中文在 === 之前；分隔符本身也可能只到达一半（"=="），按 2 个以上 = 切
    text = re.split(r"={2,}", text, maxsplit=1)[0]
    return text.strip()


# ============================================================
# 函数：_merge_bubble_segments()
# 作用：把分句列表按"<6 字短句并入前句"规则合并成气泡分段。
#       ★纯函数。_show_layered_bubbles 与流式增量分句共用，
#       保证流式期增量分段与最终分段同构（前缀稳定，只增不改前段）。
# 参数：
#   parts list[str] 已按句末标点切好的句子列表
# 返回值：list[str] —— 合并后的分段
# ============================================================
def _merge_bubble_segments(parts: list[str]) -> list[str]:
    """短句（<6 字）并入前一段，避免气泡一次只蹦两三个字。"""
    merged: list[str] = []
    for seg in parts:
        if merged and len(merged[-1]) < 6:
            merged[-1] += seg
        else:
            merged.append(seg)
    return merged


# ============================================================
# 函数：_final_bubble_segments()
# 作用：完整文本 → 最终气泡分段（_show_layered_bubbles 的分段算法抽出）。
#       ★纯函数。
# 参数：
#   text str 完整中文回复
# 返回值：list[str]
# ============================================================
def _final_bubble_segments(text: str) -> list[str]:
    """完整回复的分段（纯函数，便于单元测试）。"""
    import re

    parts = [p.strip() for p in re.split(r'(?<=[。！？!?\n])\s*', text.strip())]
    return _merge_bubble_segments([p for p in parts if p])


# ============================================================
# 函数：_split_stream_segments()
# 作用：流式中文 → (已完成的分段, 未完结尾巴)。流式增量分句核心：
#       每到句末标点即成段，用户无需等整条回复结束（更无需等 TTS 播完）
#       就能单击推进；尾巴是最后一个尚未到句末标点的残句，继续打字机。
#       完成段是最终分段的前缀（追加新句只增长末段/追加新段）。
#       ★纯函数（不碰 UI、无副作用），方便单元测试。
# 参数：
#   display str 流式当前可显示中文（_streamed_display_text 的输出）
# 返回值：(list[str], str) —— (已完成分段, 未完结尾巴)
# ============================================================
def _split_stream_segments(display: str) -> "tuple[list[str], str]":
    """流式文本 → (已完成分段, 未完结尾巴)（纯函数，便于单元测试）。"""
    import re

    text = display.strip()
    if not text:
        return [], ""
    parts = [p.strip() for p in re.split(r'(?<=[。！？!?\n])\s*', text)]
    parts = [p for p in parts if p]
    if not parts:
        return [], ""
    if re.search(r'[。！？!?\n]$', text):
        return _merge_bubble_segments(parts), ""
    return _merge_bubble_segments(parts[:-1]), parts[-1]


# ============================================================
# 函数：_decide_delta_action()
# 作用：AI 回复以流式增量（逐段）到达时，做纯逻辑决策：
#       把新内容累积进 streamed_reply，并决定显示"思考点"动画还是
#       流式更新气泡文字。★纯函数（不碰 UI、无副作用），方便单元测试。
#       决策规则：气泡模式下一旦有可显示内容（_streamed_display_text 非空）
#       即流式上屏并停止思考点动画——首字即见，不等整条回复结束；
#       尚无可显示内容（如只到达 [emotion: 前缀）时继续思考点动画；
#       终端模式激活时不显示思考点也不更新气泡（终端流式回显已替代）。
# 参数：
#   streamed_reply    str  已累积的回复内容
#   text              str  本次新到达的增量文本
#   suppress_thinking bool 是否抑制思考点动画（终端模式为 True）
# 返回值：tuple[str, bool, bool] ——
#   (新的累积内容, 是否显示思考点动画, 是否更新气泡文字)
# ============================================================
def _decide_delta_action(
    streamed_reply: str, text: str, suppress_thinking: bool
) -> tuple[str, bool, bool]:
    """流式增量到达时的纯决策逻辑。

    返回 (new_streamed, should_show_thinking, should_set_bubble_text)：
    - 始终把 text 累积到 streamed_reply；
    - 终端模式（suppress_thinking=True）：终端流式回显已替代气泡，两者都不更新；
    - 气泡模式：_streamed_display_text 非空 → 流式更新气泡文字、停止思考点；
      仍为空（如只有 [emotion: 前缀到达）→ 继续思考点动画。
    """
    new_streamed = streamed_reply + text
    if suppress_thinking:
        return new_streamed, False, False
    if _streamed_display_text(new_streamed):
        return new_streamed, False, True
    return new_streamed, True, False


# ============================================================
# 函数：_decide_send_instant_action()
# 作用：用户按下发送瞬间的"即时反应"决策——返回一个动作字典，
#       让 UI 显示呼吸动画 + thinking 情绪（"让我想想…"由动画取代）。
# 参数：无
# 返回值：dict —— {"show_thinking_dots": bool, "emotion": str}
# ============================================================
def _decide_send_instant_action() -> dict:
    """_send 发送瞬间的即时反应决策：呼吸动画 + thinking emotion。
    返回 {show_thinking_dots, emotion}。不再用静态'让我想想…'。"""
    return {"show_thinking_dots": True, "emotion": "thinking"}


# ============================================================
# 函数：_decide_call_toggle_action()
# 作用：Dock 电话按钮点击时的决策：非通话态→进入通话；通话态→挂断。
#       纯函数便于测试（同 _decide_delta_action 模式）。
# 参数：
#   in_call bool 当前是否正在通话中
# 返回值：dict —— {"enter_call": bool, "hangup": bool}
# ============================================================
def _decide_call_toggle_action(in_call: bool) -> dict:
    """Dock 电话按钮点击决策：非通话态→进入通话，通话态→挂断。

    返回 {enter_call, hangup}。纯函数便于测试（参考 _decide_delta_action 模式）。
    """
    if in_call:
        return {"enter_call": False, "hangup": True}
    return {"enter_call": True, "hangup": False}
