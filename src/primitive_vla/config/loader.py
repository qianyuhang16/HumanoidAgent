"""Load bundled defaults and merge or persist application configuration."""

import json
from pathlib import Path

from .schema import AppConfig
from primitive_vla.skills.loader import load_skills

DEFAULTS_DIR = Path(__file__).parent / "defaults"


def default_config() -> AppConfig:
    scene = json.loads((DEFAULTS_DIR / "kitchen.json").read_text())
    scene.pop("mode", None)
    return AppConfig(scene=scene, skill_sources=["builtin:kitchen"], skills=load_skills(["builtin:kitchen"]))


def load_config(path: Path | None = None) -> AppConfig:
    if path is None or not path.exists():
        return default_config()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("skill_sources"):
        data["skills"] = load_skills(data["skill_sources"], path.resolve().parent)
    elif "skills" in data:
        data["skill_sources"] = []  # Legacy or explicitly self-contained snapshots.
    if "scene" in data and "skills" in data:
        return AppConfig.model_validate(data)  # Full snapshots preserve explicit deletions.
    def merge(base, override):
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                merge(base[key], value)
            else:
                base[key] = value
        return base
    return AppConfig.model_validate(merge(default_config().model_dump(mode="json"), data))


def save_config(config: AppConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    data = config.model_dump(mode="json")
    if config.skill_sources:
        data.pop("skills")  # Skill files remain the source; tasks retain resolved snapshots.
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def resolve_skills(config: AppConfig, relative_to: Path) -> AppConfig:
    if not config.skill_sources:
        return config.model_copy(deep=True)
    data = config.model_dump()
    data["skills"] = load_skills(config.skill_sources, relative_to)
    return AppConfig.model_validate(data)
