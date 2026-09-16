"""Discover SKILL.md contracts and retain their Markdown instructions."""

from pathlib import Path

import yaml
from pydantic import TypeAdapter

from primitive_vla.domain.skills import Registry, SkillSpec

LIBRARY = Path(__file__).parent


def load_skill(path: Path) -> SkillSpec:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        if lines[0] != "---":
            raise ValueError("Expected YAML front matter")
        end = lines.index("---", 1)
        metadata = yaml.safe_load("\n".join(lines[1:end]))
        if not isinstance(metadata, dict) or "instructions" in metadata:
            raise ValueError("Skill metadata must be a mapping; instructions belong in the Markdown body")
        metadata["instructions"] = "\n".join(lines[end + 1:]).strip()
        return TypeAdapter(SkillSpec).validate_python(metadata)
    except (ValueError, IndexError, yaml.YAMLError) as error:
        raise ValueError(f"Invalid skill {path}: {error}") from error


def load_skills(sources: list[str], relative_to: Path = Path.cwd()) -> list[SkillSpec]:
    specs = []
    for source in sources:
        if source.startswith("builtin:"):
            path = (LIBRARY / source.removeprefix("builtin:")).resolve()
            if not path.is_relative_to(LIBRARY.resolve()):
                raise ValueError(f"Invalid built-in skill source: {source}")
        else:
            path = Path(source).expanduser()
            if not path.is_absolute():
                path = relative_to / path
        files = [path] if path.is_file() else sorted(p for p in path.rglob("*.md") if p.name.lower() == "skill.md")
        if not files:
            raise ValueError(f"No SKILL.md found in {path}")
        specs.extend(load_skill(file) for file in files)
    Registry(specs)
    return specs
