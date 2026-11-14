"""Explicit desktop composition: DeepSeek Planning plus the optional RoboCasa Provider."""
from contextlib import AsyncExitStack, asynccontextmanager
import json
import os
from pathlib import Path

from fastapi import FastAPI

from primitive_vla.memory import Memory
from primitive_vla.memory.organizer import MemoryOrganizer
from primitive_vla.planning import ModelPlanner
from primitive_vla.vlm.deepseek import DeepSeekVLM
from primitive_vla.providers.robocasa import RoboCasa, RoboCasaConfig
from primitive_vla.providers.robocasa.provider import ROBOT_TYPE
from primitive_vla.runtime.agent import Agent
from primitive_vla.skills import SkillStore
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.registry import ToolRegistry
from .app import RobotProfile, create_app


def create_workbench() -> FastAPI:
    config_path = Path(os.environ.get("PRIMITIVE_DEEPSEEK_CONFIG", "workspace/deepseek/config.json"))
    config = json.loads(config_path.read_text())
    workspace = Path(config.get("workspace", "workspace/deepseek/robocasa"))
    vlm = DeepSeekVLM(
        os.environ.get("DEEPSEEK_API_KEY") or config["api_key"],
        model=config.get("model", "deepseek-flash"),
    )
    scene_config = RoboCasaConfig(
        task=config.get("task", "OpenDrawer"), seed=config.get("seed", 1),
        gpu=config.get("gpu", 0), image_size=config.get("image_size", 512),
        assets_path=Path(config.get("assets_path", "workspace/robocasa-assets")),
        workdir=workspace / "native",
    )

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            memory = Memory(workspace)
            stack.callback(memory.close)
            environment = await RoboCasa.open(
                scene_config, memory,
                new_episode=os.environ.get("PRIMITIVE_ROBOCASA_NEW_EPISODE") == "1",
            )
            stack.push_async_callback(environment.close)
            registry, resources, skills = ToolRegistry(), ResourceManager(), SkillStore()
            environment.register(registry, resources, skills)
            planner = ModelPlanner(vlm, read_resource=memory.read_resource)
            agent = Agent(planner, registry, memory, skills, resources)
            workbench = create_app(agent, robots=(RobotProfile(
                scene_config.robot_id, ROBOT_TYPE, f"RoboCasa · {scene_config.task}（仿真）"),),
                organizer=MemoryOrganizer(memory, vlm),
                view_sources={scene_config.robot_id: environment.views})
            # Shutdown order: stop/drain Agent calls, close the simulator, then close storage.
            await stack.enter_async_context(workbench.router.lifespan_context(workbench))
            app.state.console = workbench.state.console
            app.mount("/", workbench)
            yield

    return FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)
