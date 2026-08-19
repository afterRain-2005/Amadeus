"""SkillManager 技能发现/加载测试：UTF-8 读取 SKILL.md、frontmatter 解析、名称回退与 prompt 构建。"""
from __future__ import annotations

from pathlib import Path

from core.skills import MAX_SKILL_BYTES, SkillManager, build_skill_prompt


def _write_skill(root: Path, name: str, description: str, body: str, version: str = "1.0") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nversion: {version}\n---\n{body}",
        encoding="utf-8",
    )
    return skill_dir


def test_discover_reads_skill_md(tmp_path):
    _write_skill(tmp_path, "greet", "say hello", "こんにちは")
    mgr = SkillManager(roots=[("test", tmp_path)])
    skills = mgr.discover()
    assert "greet" in skills
    assert skills["greet"].description == "say hello"
    assert skills["greet"].source == "test"


def test_discover_ignores_dir_without_skill_md(tmp_path):
    (tmp_path / "empty").mkdir()
    mgr = SkillManager(roots=[("test", tmp_path)])
    assert mgr.discover() == {}


def test_load_returns_parsed_content(tmp_path):
    _write_skill(tmp_path, "greet", "say hello", "hello body")
    mgr = SkillManager(roots=[("test", tmp_path)])
    loaded = mgr.load("greet")
    assert loaded.info.name == "greet"
    assert loaded.content == "hello body"


def test_load_is_case_insensitive(tmp_path):
    _write_skill(tmp_path, "Greet", "say hello", "hello body")
    mgr = SkillManager(roots=[("test", tmp_path)])
    assert mgr.load("greet").info.name == "Greet"


def test_load_missing_raises_key_error(tmp_path):
    mgr = SkillManager(roots=[("test", tmp_path)])
    try:
        mgr.load("nope")
    except KeyError:
        return
    raise AssertionError("expected KeyError for missing skill")


def test_name_falls_back_to_directory(tmp_path):
    # frontmatter name 非法（含空格）→ 回退到目录名
    skill_dir = tmp_path / "my_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text('---\nname: "bad name!"\n---\nbody', encoding="utf-8")
    mgr = SkillManager(roots=[("test", tmp_path)])
    assert "my_skill" in mgr.discover()


def test_oversized_skill_is_skipped(tmp_path):
    skill_dir = tmp_path / "big"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("x" * (MAX_SKILL_BYTES + 1), encoding="utf-8")
    mgr = SkillManager(roots=[("test", tmp_path)])
    assert "big" not in mgr.discover()


def test_build_skill_prompt_empty():
    assert build_skill_prompt([]) == ""


def test_build_skill_prompt_includes_name_and_content(tmp_path):
    _write_skill(tmp_path, "greet", "say hello", "hello body")
    mgr = SkillManager(roots=[("test", tmp_path)])
    prompt = build_skill_prompt([mgr.load("greet")])
    assert "skill: greet" in prompt
    assert "hello body" in prompt
