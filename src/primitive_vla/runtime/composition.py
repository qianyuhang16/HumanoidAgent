"""Configuration driven adapter composition for an interactive Agent run.

The Agent loop only knows its client, tool interface and lifecycle limits. A
scene, policy, VLM transport, or skill library is selected by the composition
document and loaded at this seam.
"""

import importlib
import inspect
from pathlib import Path

from primitive_vla.config.schema import VLMConfig


def load_adapter(path: str):
    """Load ``package.module:object`` named by a configuration document."""
    try:
        module_name, object_name = path.split(":", 1)
        return getattr(importlib.import_module(module_name), object_name)
    except (ValueError, ImportError, AttributeError) as error:
        raise ValueError(f"Invalid adapter path {path!r}") from error


def adapter_path(config: dict, slot: str) -> str:
    adapters = config.get("adapters", {})
    value = adapters.get(slot)
    if not isinstance(value, str) or ":" not in value:
        raise ValueError(f"Configuration must provide adapters.{slot} as module:path")
    return value


def create_scene(config: dict, directory: Path, log):
    """Create the configured scene adapter behind the Agent's observation seam."""
    slot = "scene" if config.get("adapters", {}).get("scene") else "backend"
    return load_adapter(adapter_path(config, slot))(config, directory, log)


create_backend = create_scene  # compatibility for callers outside the composition root


def create_tools(config: dict, backend, directory: Path, log):
    return load_adapter(adapter_path(config, "tools"))(backend, directory, log)


def create_vlm(config: dict, directory: Path, log):
    factory = load_adapter(adapter_path(config, "vlm"))
    return factory(VLMConfig.model_validate(config["vlm"]), directory / "media", log=log)


def create_vlm_client(vlm_config, directory: Path, *, adapter: str, api_key=None, log=None, transport=None):
    """Create a VLM through the configured provider adapter."""
    return load_adapter(adapter)(vlm_config, directory, api_key, log, transport)


def create_agent(config: dict, client, tools, prompt: str, log):
    agent = load_adapter(adapter_path(config, "agent"))
    limits = config.get("agent", config.get("tool_agent", {}))
    return agent(client, tools, prompt, log,
                 max_calls=limits.get("max_calls", 32),
                 timeout_s=config.get("task", {}).get("timeout_s", 3600))


def create_policy(config: dict, directory: Path | None = None):
    """Create a simulator policy from its configured policy adapter."""
    factory = load_adapter(config["policy_adapter"])
    if directory is not None and len(inspect.signature(factory).parameters) >= 2:
        return factory(config, directory)
    return factory(config)
