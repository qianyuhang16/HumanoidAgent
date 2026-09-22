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


def load_catalog(sources: list[str], relative_to: Path = Path.cwd()) -> Registry:
    """Composition adapter returning the validated skill catalog."""
    return Registry(load_skills(sources, relative_to))


def validate_controller_program(spec: SkillSpec, *, approach_only: bool) -> None:
    """Validate the selected execution strategy once the scene is known."""
    execution_type = spec.execution.get("type")
    if execution_type not in ("program", "policy"):
        raise ValueError(f"{spec.name}: execution.type must be program or policy")
    if execution_type == "program" and not isinstance(spec.execution.get("steps", []), list):
        raise ValueError(f"{spec.name}: program execution requires steps")
    if execution_type == "policy" and not isinstance(spec.execution.get("prompt"), str):
        raise ValueError(f"{spec.name}: policy execution requires prompt")
    if approach_only and execution_type != "policy":
        for step in spec.execution.get("steps", []):
            if step.get('op') not in ('point', 'move', 'phase') or step.get('kind') not in (None, 'approach'):
                raise ValueError(f"{spec.name}: policy mode permits only approach motion through the scripted/IK path")
