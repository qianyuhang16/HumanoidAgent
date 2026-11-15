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
from primitive_vla.vlm.config import VLMConfig

RUNTIME_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["connection", "profile", "run_dir"],
    "properties": {
        "connection": {
            "type": "object", "additionalProperties": False, "required": ["command", "cwd"],
            "properties": {
                "command": {"type": "string", "minLength": 1},
                "args": {"type": "array", "items": {"type": "string"}},
                "cwd": {"type": "string", "minLength": 1},
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
            },
        },
        "profile": {"type": "string", "minLength": 1},
        "run_dir": {"type": "string", "minLength": 1},
        "options": {"type": "object"},
    },
}


def create_workbench(vlm_config_path: str | Path | None = None,
                     runtime_config_path: str | Path | None = None) -> FastAPI:
    vlm_path = Path(vlm_config_path or os.environ.get("PRIMITIVE_VLM_CONFIG", "configs/vlm.json"))
    path = Path(runtime_config_path or os.environ.get("PRIMITIVE_RUNTIME_CONFIG", "configs/runtime.json"))
    config = json.loads(path.read_text())
    error = next(Draft202012Validator(RUNTIME_SCHEMA).iter_errors(config), None)
    if error is not None:
        # Config errors must never echo an API key or the enclosing config object.
        raise ValueError(f"invalid runtime config at {'.'.join(map(str, error.absolute_path)) or 'root'}")
    vlm = VLMConfig.load(vlm_path).connect()
    connection = {**config["connection"], 'cwd': str((path.parent / config['connection']['cwd']).resolve())}
    run_dir = (path.parent / config["run_dir"]).resolve()

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            memory = Memory(run_dir)
            stack.callback(memory.close)
            binding, skills = await stack.enter_async_context(open_platform(
                connection, config["profile"], memory=memory, workdir=run_dir / "native",
                options=config.get("options", {})))
            planner = ModelPlanner(vlm, read_resource=memory.read_resource)
            agent = Agent(planner, binding.registry, memory, skills, binding.resources)
            workbench = create_app(agent, robots=(RobotProfile(binding.robot_id, binding.robot_type, binding.label),),
                                   organizer=MemoryOrganizer(memory, vlm),
                                   view_sources={binding.robot_id: binding.views} if binding.views else {})
            @workbench.get("/api/assembly")
            async def assembly_catalog():
                return {"platforms": [binding.platform],
                        "selected": {"platform": binding.platform['id'], "profile": binding.profile_id}}

            await stack.enter_async_context(workbench.router.lifespan_context(workbench))
            app.state.console = workbench.state.console
            app.mount("/", workbench)
            yield

    return FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)
