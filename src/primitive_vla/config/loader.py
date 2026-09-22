"""Load bundled defaults and merge or persist application configuration."""

from pathlib import Path
import yaml

from .schema import AppConfig
from .document import load_document
from primitive_vla.runtime.composition import load_adapter

DEFAULTS_DIR = Path(__file__).parent / "defaults"
DEFAULT_SKILL_LOADER = "primitive_vla.skills.loader:load_catalog"


def default_config() -> AppConfig:
    scene = load_document(DEFAULTS_DIR / "kitchen.yaml")
    adapters = load_document(DEFAULTS_DIR / "adapters.yaml")
    catalog = load_adapter(adapters["skill_loader"])(["builtin:kitchen"])
    skills = catalog.specs
    return AppConfig(scene=scene, adapters=adapters, skill_sources=["builtin:kitchen"],
                     skills=skills)


def load_config(path: Path | None = None) -> AppConfig:
    if path is None or not path.exists():
        return default_config()
    data = load_document(path)
    if data.get("skill_sources"):
        loader = load_adapter(data.get("adapters", {}).get("skill_loader", DEFAULT_SKILL_LOADER))
        catalog = loader(data["skill_sources"], path.resolve().parent)
        data["skills"] = catalog.specs
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
    temporary = path.with_suffix(path.suffix + ".tmp")
    data = config.model_dump(mode="json")
    if config.skill_sources:
        data.pop("skills")  # Skill files remain the source; tasks retain resolved snapshots.
    temporary.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temporary.replace(path)


def resolve_skills(config: AppConfig, relative_to: Path) -> AppConfig:
    if not config.skill_sources:
        return config.model_copy(deep=True)
    data = config.model_dump()
    loader = load_adapter(config.adapters.skill_loader)
    data["skills"] = loader(config.skill_sources, relative_to).specs
    return AppConfig.model_validate(data)
