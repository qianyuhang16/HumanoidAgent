"""Desktop host: join independent VLM settings and platform/profile assembly."""
from contextlib import AsyncExitStack, asynccontextmanager
import json
import os
from pathlib import Path

from fastapi import FastAPI
from jsonschema import Draft202012Validator

from primitive_vla.interfaces.web.app import RobotProfile, create_app
from primitive_vla.memory import Memory
from primitive_vla.memory.organizer import MemoryOrganizer
from primitive_vla.planning import ModelPlanner
from primitive_vla.runtime.agent import Agent
from primitive_vla.runtime.connections.stdio import open_platform
from primitive_vla.tools.assembly import AssemblyCatalog
from primitive_vla.vlm.config import VLMConfig

CONFIG_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["vlm", "assembly", "workspace", "platforms"],
    "properties": {
        "workspace": {"type": "string", "minLength": 1},
        "platforms": {"type": "string", "minLength": 1},
        "vlm": {"type": "object", "additionalProperties": False, "required": ["provider", "model"],
                "properties": {"provider": {"enum": ["deepseek", "gpt"]}, "model": {"type": "string"},
                               "api_key": {"type": "string"}, "api_key_env": {"type": "string"},
                               "base_url": {"type": "string"}, "timeout": {"type": "number"},
                               "max_tokens": {"type": "integer"}}},
        "assembly": {"type": "object", "additionalProperties": False, "required": ["platform", "profile"],
                     "properties": {"platform": {"type": "string"}, "profile": {"type": "string"},
                                    "options": {"type": "object"}}},
    },
}


def create_workbench(config_path: str | Path | None = None, *, catalog=None) -> FastAPI:
    path = Path(config_path or os.environ.get("PRIMITIVE_CONFIG", "config.json"))
    config = json.loads(path.read_text())
    error = next(Draft202012Validator(CONFIG_SCHEMA).iter_errors(config), None)
    if error is not None:
        # Config errors must never echo an API key or the enclosing config object.
        raise ValueError(f"invalid application config at {'.'.join(map(str, error.absolute_path)) or 'root'}")
    catalog = catalog if catalog is not None else AssemblyCatalog(path.parent / config["platforms"])
    selection = config["assembly"]
    platform, profile = catalog.select(selection["platform"], selection["profile"])
    vlm = VLMConfig(**config["vlm"]).connect()
    workspace = (path.parent / config["workspace"]).resolve()

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            memory = Memory(workspace)
            stack.callback(memory.close)
            binding, skills = await stack.enter_async_context(open_platform(
                platform, profile, memory=memory, workdir=workspace / "native",
                options=selection.get("options", {})))
            planner = ModelPlanner(vlm, read_resource=memory.read_resource)
            agent = Agent(planner, binding.registry, memory, skills, binding.resources)
            workbench = create_app(agent, robots=(RobotProfile(binding.robot_id, binding.robot_type, binding.label),),
                                   organizer=MemoryOrganizer(memory, vlm),
                                   view_sources={binding.robot_id: binding.views} if binding.views else {})
            @workbench.get("/api/assembly")
            async def assembly_catalog():
                return {"platforms": catalog.catalog(),
                        "selected": {"platform": platform.id, "profile": profile.id},
                        "vlm": {"provider": config["vlm"]["provider"], "model": vlm.model}}

            await stack.enter_async_context(workbench.router.lifespan_context(workbench))
            app.state.console = workbench.state.console
            app.mount("/", workbench)
            yield

    return FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)
