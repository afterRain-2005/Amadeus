# tests/test_screen_context.py — 聊天屏幕感知（core/screen_context.py）
# 覆盖：开关关闭零请求 / 截屏描述 / 缓存命中不重复请求 /
#       失败写缓存短周期不重试 / prompt 组装。
import sys
import types

import pytest

from core import screen_context


@pytest.fixture(autouse=True)
def clean_cache():
    screen_context.reset_screen_context_cache()
    yield
    screen_context.reset_screen_context_cache()


def _fake_mss(monkeypatch, grab_calls):
    """构造可导入的假 mss 模块（记录 grab 次数）。"""
    frame = types.SimpleNamespace(bgra=b"\x00" * 16, width=4, height=4)

    class FakeSCT:
        monitors = [types.SimpleNamespace(left=0), types.SimpleNamespace(left=0)]

        def grab(self, monitor):
            grab_calls.append(monitor)
            return frame

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake = types.ModuleType("mss")
    fake.mss = lambda: FakeSCT()
    monkeypatch.setitem(sys.modules, "mss", fake)


def test_disabled_returns_empty_and_never_grabs(monkeypatch):
    grabs = []
    _fake_mss(monkeypatch, grabs)
    assert screen_context.describe_current_screen({"screen_awareness": {"enabled": False}}) == ""
    assert screen_context.describe_current_screen({}) == ""  # 默认关
    assert grabs == []


def test_describe_with_vision(monkeypatch):
    grabs = []
    _fake_mss(monkeypatch, grabs)
    monkeypatch.setattr(
        screen_context, "describe_screen",
        lambda frame, endpoint, api_key, model: f"desc:{endpoint}:{model}",
    )
    cfg = {"screen_awareness": {"enabled": True, "vision_endpoint": "http://v/v1", "vision_model": "gpt-4o"}}
    out = screen_context.describe_current_screen(cfg)
    assert out == "desc:http://v/v1:gpt-4o"
    assert len(grabs) == 1


def test_cache_within_interval_skips_recapture(monkeypatch):
    grabs = []
    _fake_mss(monkeypatch, grabs)
    calls = []

    def fake_desc(frame, endpoint, api_key, model):
        calls.append(1)
        return "在写代码"

    monkeypatch.setattr(screen_context, "describe_screen", fake_desc)
    cfg = {"screen_awareness": {"enabled": True, "interval_seconds": 600}}
    assert screen_context.describe_current_screen(cfg) == "在写代码"
    assert screen_context.describe_current_screen(cfg) == "在写代码"
    assert len(calls) == 1, "缓存有效期内不应重复截屏/请求"
    # force 跳过缓存
    assert screen_context.describe_current_screen(cfg, force=True) == "在写代码"
    assert len(calls) == 2


def test_failure_cached_short_term(monkeypatch):
    grabs = []
    _fake_mss(monkeypatch, grabs)
    calls = []

    def failing(frame, endpoint, api_key, model):
        calls.append(1)
        return ""

    monkeypatch.setattr(screen_context, "describe_screen", failing)
    cfg = {"screen_awareness": {"enabled": True}}
    assert screen_context.describe_current_screen(cfg) == ""
    assert screen_context.describe_current_screen(cfg) == ""
    assert len(calls) == 1, "失败结果也应进缓存，避免逐条消息重复失败请求"


def test_build_screen_prompt(monkeypatch):
    import core.screen_context as sc
    # 禁用 → 空串（不触发 describe）
    assert sc.build_screen_prompt({"screen_awareness": {"enabled": False}}) == ""
    monkeypatch.setattr(
        sc, "describe_current_screen", lambda config, force=False: "在看文档"
    )
    assert sc.build_screen_prompt({}) == "[屏幕感知] 用户当前屏幕：在看文档"
    # 描述为空 → 不产出片段
    monkeypatch.setattr(sc, "describe_current_screen", lambda config, force=False: "")
    assert sc.build_screen_prompt({"screen_awareness": {"enabled": True}}) == ""


def test_phone_vision_fallback(monkeypatch):
    grabs = []
    _fake_mss(monkeypatch, grabs)
    seen = {}

    def fake_desc(frame, endpoint, api_key, model):
        seen.update(endpoint=endpoint, api_key=api_key)
        return "ok"

    monkeypatch.setattr(screen_context, "describe_screen", fake_desc)
    cfg = {
        "phone": {"vision_endpoint": "http://phone/v1", "vision_api_key": "pk"},
        "screen_awareness": {"enabled": True},
    }
    assert screen_context.describe_current_screen(cfg) == "ok"
    assert seen == {"endpoint": "http://phone/v1", "api_key": "pk"}
