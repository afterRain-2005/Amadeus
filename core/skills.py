"""UTF-8 skill discovery and loading for the terminal agent."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from core.storage import APP_DIR


MAX_SKILL_BYTES = 512 * 1024


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    path: Path
    source: str
    version: str = ""


@dataclass(frozen=True)
class LoadedSkill:
    info: SkillInfo
    content: str


def default_skill_roots() -> list[tuple[str, Path]]:
    root = Path(__file__).resolve().parent.parent
    return [
        ("user", Path.home() / ".amadeus" / "skills"),
        ("data", APP_DIR / "skills"),
        ("project", root / "skills"),
    ]


def _safe_read_text(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise FileNotFoundError(str(path)) from exc
    if size > MAX_SKILL_BYTES:
        raise ValueError(f"Skill file is too large: {path}")
    return path.read_text(encoding="utf-8")


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _parse_skill(path: Path, source: str) -> tuple[SkillInfo, str]:
    text = _safe_read_text(path)
    metadata: dict[str, str] = {}
    body = text
    match = _FRONTMATTER_RE.match(text)
    if match:
        body = text[match.end():]
        for raw_line in match.group(1).splitlines():
            if ":" not in raw_line:
                continue
            key, value = raw_line.split(":", 1)
            metadata[key.strip().lower()] = value.strip().strip("\"'")

    fallback_name = path.parent.name
    name = metadata.get("name") or fallback_name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
        name = fallback_name
    info = SkillInfo(
        name=name,
        description=metadata.get("description", "").strip(),
        version=metadata.get("version", "").strip(),
        path=path,
        source=source,
    )
    return info, body.strip()


class SkillManager:
    """Discover skills from user/data/project roots and load SKILL.md as UTF-8."""

    def __init__(self, roots: list[tuple[str, Path]] | None = None) -> None:
        self.roots = roots or default_skill_roots()

    def discover(self) -> dict[str, SkillInfo]:
        skills: dict[str, SkillInfo] = {}
        for source, root in reversed(self.roots):
            if not root.exists() or not root.is_dir():
                continue
            for skill_dir in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.is_file():
                    continue
                try:
                    info, _body = _parse_skill(skill_file, source)
                except (OSError, UnicodeError, ValueError):
                    continue
                skills[info.name] = info
        return dict(sorted(skills.items(), key=lambda item: item[0].lower()))

    def load(self, name: str) -> LoadedSkill:
        skills = self.discover()
        info = skills.get(name)
        if info is None:
            lowered = name.lower()
            info = next((item for key, item in skills.items() if key.lower() == lowered), None)
        if info is None:
            raise KeyError(name)
        parsed_info, body = _parse_skill(info.path, info.source)
        return LoadedSkill(parsed_info, body)


def build_skill_prompt(skills: list[LoadedSkill]) -> str:
    if not skills:
        return ""
    parts = ["The following terminal skills are enabled. Follow them when relevant:"]
    for skill in skills:
        parts.append(f"\n--- skill: {skill.info.name} ---\n{skill.content}")
    return "\n".join(parts).strip()
