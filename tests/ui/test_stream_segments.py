# ============================================================
# tests/test_stream_segments.py
# 作用：流式增量分句的单元测试（纯函数，无需 QApplication）。
# 分段算法：_merge_bubble_segments（<6 字并入前句）、
#          _final_bubble_segments（完整文本最终分段）、
#          _split_stream_segments（流式 → 完成分段 + 未完结尾巴）。
# 关键性质（流式期间可单击推进的正确性基础）：
#   1. 完成分段是最终分段的前缀（末段只增长、段数只增加）
#   2. 尾巴完结成句后，最终分段与增量分段衔接无跳变
# ============================================================
import desktop_pet


# ===== _merge_bubble_segments：短句合并 =====

def test_merge_keeps_long_sentences_separate():
    parts = ["第一句的内容比较长。", "第二句的内容也比较长！"]
    assert desktop_pet._merge_bubble_segments(parts) == parts


def test_merge_absorbs_short_sentence():
    # "哦？" 2 字 < 6 → 并入前句
    assert desktop_pet._merge_bubble_segments(["你是谁？哦？"]) == ["你是谁？哦？"]
    assert desktop_pet._merge_bubble_segments(["短。", "也很短！"]) == ["短。也很短！"]


def test_merge_empty():
    assert desktop_pet._merge_bubble_segments([]) == []


# ===== _final_bubble_segments：与旧 _show_layered_bubbles 行为一致 =====

def test_final_segments_real_reply():
    text = ("（歪头）哦？你是谁？第一次见面呢...我是牧濑红莉栖的记忆体，"
            "你可以叫我Amadeus。你叫什么名字？")
    assert desktop_pet._final_bubble_segments(text) == [
        "（歪头）哦？",
        "你是谁？第一次见面呢...我是牧濑红莉栖的记忆体，你可以叫我Amadeus。",
        "你叫什么名字？",
    ]


def test_final_segments_empty():
    assert desktop_pet._final_bubble_segments("") == []
    assert desktop_pet._final_bubble_segments("   ") == []


# ===== _split_stream_segments：流式增量切分 =====

def test_split_no_boundary_yet_all_tail():
    segs, tail = desktop_pet._split_stream_segments("你好，我是牧濑红")
    assert segs == []
    assert tail == "你好，我是牧濑红"


def test_split_first_sentence_completed():
    segs, tail = desktop_pet._split_stream_segments("第一句的内容比较长。第二句还在生")
    assert segs == ["第一句的内容比较长。"]
    assert tail == "第二句还在生"


def test_split_ends_with_punctuation_no_tail():
    segs, tail = desktop_pet._split_stream_segments("第一句的内容比较长。第二句也结束了！")
    assert segs == ["第一句的内容比较长。", "第二句也结束了！"]
    assert tail == ""


def test_split_empty():
    assert desktop_pet._split_stream_segments("") == ([], "")
    assert desktop_pet._split_stream_segments("  ") == ([], "")


def test_split_newline_counts_as_boundary():
    segs, tail = desktop_pet._split_stream_segments("第一行内容\n第二行还没")
    assert segs == ["第一行内容"]
    assert tail == "第二行还没"


# ===== 关键性质：增量分段与最终分段的索引兼容 =====

def test_incremental_segments_are_final_prefix():
    """流式任意时刻的完成分段，必须是最终分段的前缀（末段可增长）。
    否则 _agent_finished 同步最终分段时，用户已读的句序会错位。"""
    full = ("（歪头）哦？你是谁？第一次见面呢...我是牧濑红莉栖的记忆体，"
            "你可以叫我Amadeus。你叫什么名字？")
    final = desktop_pet._final_bubble_segments(full)
    # 模拟逐字流式：每个截断点切一次，完成分段必须与最终分段前缀兼容
    for cut in range(1, len(full) + 1):
        segs, _tail = desktop_pet._split_stream_segments(full[:cut])
        # 检查 segs 是 final 的前缀（允许末段增长：segs[k] 是 final[k] 的前缀）
        assert len(segs) <= len(final), f"cut={cut} segs={segs}"
        for i in range(len(segs) - 1):
            assert segs[i] == final[i], f"cut={cut} 第{i}段不稳定: {segs[i]!r} != {final[i]!r}"
        if segs:
            last = segs[-1]
            assert any(f.startswith(last) or last.startswith(f)
                       for f in final[len(segs) - 1:]), f"cut={cut} 末段 {last!r} 与最终分段不衔接"
