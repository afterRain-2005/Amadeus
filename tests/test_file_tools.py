"""文件工具 + 路径校验测试。"""
import os
from pathlib import Path
import tempfile
from unittest.mock import patch


def test_validate_path_rejects_traversal():
    from core.desktop_tools import _validate_path
    # 向上多级逃出所有允许根（home/桌面/项目根），resolve 后到盘根外
    ok, _ = _validate_path("../../../../../evil")
    assert ok is False


def test_validate_path_rejects_system_dir():
    from core.desktop_tools import _validate_path
    ok, _ = _validate_path("C:/Windows/System32/calc.exe")
    assert ok is False


def test_validate_path_accepts_user_file():
    from core.desktop_tools import _validate_path
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(b"hi")
        path = f.name
    try:
        ok, resolved = _validate_path(path)
        assert ok is True
        assert resolved.exists()
    finally:
        os.unlink(path)


def test_file_find_returns_matches():
    from core.desktop_tools import execute_tool
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "alpha.txt").write_text("x")
        (Path(d) / "beta.md").write_text("y")
        result = execute_tool("file_find", {"pattern": "*.txt", "root": d})
    assert "alpha.txt" in result["text"]
    assert "beta.md" not in result["text"]


def test_list_dir_lists_entries():
    from core.desktop_tools import execute_tool
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "a.txt").write_text("x")
        result = execute_tool("list_dir", {"path": d})
    assert "a.txt" in result["text"]


def test_read_file_returns_content():
    from core.desktop_tools import execute_tool
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8") as f:
        f.write("hello红莉栖")
        path = f.name
    try:
        result = execute_tool("read_file", {"path": path})
        assert "hello红莉栖" in result["text"]
    finally:
        os.unlink(path)


def test_read_file_rejects_traversal():
    from core.desktop_tools import execute_tool
    result = execute_tool("read_file", {"path": "../../../../etc/passwd"})
    assert "拒绝" in result["text"] or "denied" in result["text"].lower()
