"""Composition root for the canonical Agent runtime.

Only this module resolves configured adapter paths. The Agent and domain
modules depend on contracts, never on concrete scenes, policies, or providers.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path


def load_adapter(path: str):
    if not isinstance(path, str) or path.count(":") != 1:
        raise ValueError(f"Invalid adapter path: {path!r}")
    module_name, object_name = path.split(":", 1)
    try:
        return getattr(importlib.import_module(module_name), object_name)
    except (ImportError, AttributeError) as error:
        raise ValueError(f"Cannot load adapter {path!r}") from error


def instantiate(path: str, **kwargs):
    factory = load_adapter(path)
    parameters = inspect.signature(factory).parameters
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return factory(**kwargs)
    accepted = {name: value for name, value in kwargs.items() if name in parameters}
    return factory(**accepted)


def create_vlm_client(vlm_config, directory: Path, *, adapter: str, api_key=None, log=None, transport=None):
    return load_adapter(adapter)(vlm_config, directory, api_key, log, transport)


def validate_adapter(path: str, required: tuple[str, ...]) -> None:
    adapter = load_adapter(path)
    missing = [name for name in required if not hasattr(adapter, name)]
    if missing:
        raise TypeError(f"Adapter {path!r} is missing: {', '.join(missing)}")
