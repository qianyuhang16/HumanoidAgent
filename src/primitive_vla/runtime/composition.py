"""Resolve configured factories without importing robot or scene implementations."""

from __future__ import annotations

import importlib


def load_adapter(path: str):
    if not isinstance(path, str) or path.count(":") != 1:
        raise ValueError(f"Invalid adapter path: {path!r}")
    module_name, object_name = path.split(":", 1)
    try:
        return getattr(importlib.import_module(module_name), object_name)
    except (ImportError, AttributeError) as error:
        raise ValueError(f"Cannot load adapter {path!r}") from error
