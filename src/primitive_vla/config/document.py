"""Load configuration documents without making the runtime depend on a format."""

import json
from pathlib import Path

import yaml


def load_document(path: Path):
    """Read a JSON or YAML document and return its mapping/list value."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    value = yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    if value is None:
        return {}
    return value
