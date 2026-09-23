"""Versioned tool configuration and explicit migration of old scene configs."""

from pathlib import Path
import yaml

from .schema import AdapterSpec, AgentLimits, AppConfig, LegacyConfig
from .document import load_document
from primitive_vla.runtime.composition import load_adapter

DEFAULTS_DIR = Path(__file__).parent / "defaults"


def default_config() -> AppConfig:
    return AppConfig()


def legacy_config(data: dict, relative_to: Path) -> LegacyConfig:
    data = dict(data)
    if "scene" not in data:
        data["scene"] = load_document(DEFAULTS_DIR / "kitchen.yaml")
    if data.get("skill_sources") or "skills" not in data:
        sources = data.get("skill_sources", ["builtin:kitchen"])
        loader = load_adapter(data.get("adapters", {}).get(
            "skill_loader", "primitive_vla.skills.loader:load_catalog"))
        data["skills"] = list(loader(sources, relative_to).specs.values())
    return LegacyConfig.model_validate(data)


def migrate_legacy(data: dict, relative_to: Path) -> AppConfig:
    legacy = legacy_config(data, relative_to)
    # The migrated configuration contains resolved skill contracts; snapshots
    # must not reopen relative paths from a different config directory later.
    legacy.skill_sources = []
    return AppConfig(
        name=legacy.name, web=legacy.web,
        model=AdapterSpec(adapter="primitive_vla.agent.model:JSONModel",
                          options=legacy.vlm.model_dump()),
        tool_providers=[AdapterSpec(adapter="primitive_vla.tools.legacy:SceneTools",
                                   options={"config": legacy.model_dump(mode="json")})],
        limits=AgentLimits(max_calls=legacy.task_defaults.max_steps * 3,
                           max_turns=legacy.task_defaults.max_steps * 3,
                           timeout_s=legacy.task_defaults.timeout_s,
                           stop_timeout_s=legacy.execution.stop_timeout_s),
        system_prompt="Use scene.observe to inspect the environment. Legacy skills are individual tools; choose their arguments and order from the task and observations."
    )


def load_config(path: Path | None = None) -> AppConfig:
    if path is None:
        return default_config()
    if not path.exists():
        raise FileNotFoundError(path)
    data = load_document(path)
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a mapping")
    if "schema_version" not in data and any(key in data for key in ("scene", "backends", "skill_sources", "planner")):
        return migrate_legacy(data, path.resolve().parent)
    return AppConfig.model_validate(data)


def save_config(config: AppConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(yaml.safe_dump(config.model_dump(mode="json"),
                                        allow_unicode=True, sort_keys=False), encoding="utf-8")
    temporary.replace(path)
