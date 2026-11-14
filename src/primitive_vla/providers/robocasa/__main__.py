"""Real environment check: no Planner, model weights, or task-solving script."""
import argparse
import asyncio
from dataclasses import asdict
from importlib.metadata import version
import json
from pathlib import Path

from primitive_vla.events import EventBus
from primitive_vla.memory import Memory
from primitive_vla.skills import SkillStore
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.hooks import Hooks
from primitive_vla.tools.registry import ToolRegistry
from primitive_vla.tools.router import ToolRouter
from .provider import ROBOT_TYPE, RoboCasa, RoboCasaConfig


async def smoke(args):
    import rpent
    memory = Memory(args.workspace)
    config = RoboCasaConfig(task=args.task, split=args.split, seed=args.seed, gpu=args.gpu,
                           image_size=args.image_size, assets_path=args.assets,
                           workdir=args.workspace / "native")
    integration = await RoboCasa.open(config, memory, new_episode=args.new_episode)
    registry, resources, skills = ToolRegistry(), ResourceManager(), SkillStore()
    integration.register(registry, resources, skills)
    router = ToolRouter(registry, resources, Hooks(memory, EventBus()))
    session_id = f"smoke:{integration.episode_id}"

    async def call(name, arguments):
        current = await router.invoke(name, arguments, session_id=session_id,
                                      task_id=config.task, robot_id=config.robot_id,
                                      robot_type=ROBOT_TYPE)
        await current.available.wait()
        if current.status == "running":
            await current.done.wait()
        if current.status != "completed":
            raise RuntimeError(json.dumps(current.snapshot(), ensure_ascii=False, default=str))
        return current

    try:
        before = await call("state.robocasa.capture_image", {"camera": "navview", "depth": True})
        pixel = {"camera": "navview", "row": args.image_size // 2,
                 "column": args.image_size // 2,
                 "episode_id": before.result.payload["episode_id"],
                 "simulation_steps": before.result.payload["simulation_steps"]}
        localized = await call("state.robocasa.localize_pixel", pixel)
        for invalid in ({"steps": 0}, {"gripper": "invalid"}, {"forward": 2}):
            rejected = await router.invoke(
                "execution.robocasa.move_base", invalid, session_id=session_id,
                task_id=config.task, robot_id=config.robot_id, robot_type=ROBOT_TYPE,
            )
            if rejected.status != "failed" or rejected.error.kind.value != "interface_error":
                raise RuntimeError("invalid primitive arguments were not rejected at entry")
        observed = await call("state.robocasa.observe", {})
        if observed.result.payload["simulation_steps"] != before.result.payload["simulation_steps"]:
            raise RuntimeError("rejected input advanced the simulation")
        action = await call("execution.robocasa.move_base", {"forward": 0.2, "steps": 8})
        stale = await router.invoke(
            "state.robocasa.localize_pixel", pixel, session_id=session_id,
            task_id=config.task, robot_id=config.robot_id, robot_type=ROBOT_TYPE,
        )
        if stale.status != "failed" or stale.error.kind.value != "skill_condition":
            raise RuntimeError("stale camera projection was not rejected")
        after = await call("state.robocasa.capture_image", {"camera": "navview"})
        check = await call("verification.robocasa.task", {"related_call_id": action.id})
        first_position = before.result.payload["robot_state"]["robot0_base_pos"]
        last_position = after.result.payload["robot_state"]["robot0_base_pos"]
        distance = sum((a - b) ** 2 for a, b in zip(first_position, last_position)) ** 0.5
        images = []
        for label, result in (("before", before.result), ("after", after.result)):
            content = memory.read_resource(result.payload["resources"]["rgb"])
            (args.workspace / f"{label}.png").write_bytes(content)
            images.append(content)
        if distance <= 1e-5 or images[0] == images[1]:
            raise RuntimeError("base motion/camera change was not observed")
        report = {
            "environment_check_passed": True,
            "invalid_arguments_rejected_without_motion": True,
            "stale_pixel_projection_rejected": True,
            "sample_pixel_world": localized.result.payload["point_world"],
            "task_success": check.result.verification.conclusion,
            "scope": "camera, depth projection, native base motion, resource journal, oracle verification; no autonomous planning",
            "config": asdict(config), "episode_id": integration.episode_id,
            "base_displacement_metres": distance,
            "tools": [spec.name for spec in registry.catalog()],
            "action_call_id": action.id, "verification_call_id": check.id,
            "rpent_version": rpent.__version__, "rpent_source": rpent.__file__,
            "versions": {package: version(package)
                         for package in ("rpent-robocasa365", "robosuite", "mujoco")},
        }
        text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        (args.workspace / "smoke-report.json").write_text(text, encoding="utf-8")
        print(text)
    finally:
        await router.close()
        await integration.close()
        memory.close()


def main():
    parser = argparse.ArgumentParser(description="RoboCasa real-simulator integration smoke check")
    parser.add_argument("--task", default="OpenDrawer")
    parser.add_argument("--split", choices=["target", "pretrain", "all"], default="target")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--image-size", type=int, default=128)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path("workspace/robocasa-smoke"))
    parser.add_argument("--new-episode", action="store_true",
                        help="explicitly start new physics; never resume old sessions against it")
    asyncio.run(smoke(parser.parse_args()))


if __name__ == "__main__":
    main()
