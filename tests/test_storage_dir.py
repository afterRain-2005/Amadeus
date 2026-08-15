"""storage 数据目录解析测试：frozen 下必须落在 exe 旁而非 _MEIPASS。"""
import sys
from pathlib import Path
from unittest.mock import patch


def test_resolve_dev_dir():
    """dev 模式：项目根 data/。"""
    from core.storage import _resolve_app_dir
    with patch.object(sys, "frozen", False, create=True):
        resolved = _resolve_app_dir()
    assert resolved == Path(__file__).resolve().parents[1] / "data"


def test_resolve_frozen_dir(tmp_path):
    """frozen onefile：exe 同级 data/，不用 _MEIPASS（随机临时目录会丢配置）。"""
    from core.storage import _resolve_app_dir
    exe = tmp_path / "Amadeus.exe"
    exe.write_bytes(b"")
    with patch.object(sys, "frozen", True, create=True), \
         patch.object(sys, "executable", str(exe)):
        resolved = _resolve_app_dir()
    assert resolved == tmp_path / "data"


def test_resolve_env_overrides(tmp_path):
    """AMADEUS_DATA_DIR 环境变量最高优先级。"""
    import os
    from core.storage import _resolve_app_dir
    with patch.dict(os.environ, {"AMADEUS_DATA_DIR": str(tmp_path)}):
        resolved = _resolve_app_dir()
    assert resolved == tmp_path
