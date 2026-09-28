"""Load and save the generic versioned Agent configuration."""

from pathlib import Path
import yaml

from .schema import AppConfig
from .document import load_document


def default_config() -> AppConfig:
    return AppConfig()


def load_config(path: Path | None = None) -> AppConfig:
    if path is None:
        return default_config()
    if not path.exists():
        raise FileNotFoundError(path)
    data = load_document(path)
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a mapping")
    return AppConfig.model_validate(data)


def save_config(config: AppConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(yaml.safe_dump(config.model_dump(mode="json"),
                                        allow_unicode=True, sort_keys=False), encoding="utf-8")
    temporary.replace(path)
