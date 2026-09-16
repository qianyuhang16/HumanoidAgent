"""Command-line entry points sharing the application task service."""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from primitive_vla.agent.task_parser import DEFAULT_INSTRUCTION
from primitive_vla.config.loader import default_config, load_config, save_config
from primitive_vla.config.schema import AppConfig
from primitive_vla.runtime.service import TaskService
from primitive_vla.skills.loader import load_skills
from dataclasses import asdict


async def run(args):
    service = TaskService(args.workspace, args.config)
    try:
        config = service.config.model_dump(mode="json")
        if args.scene:
            scene = json.loads(args.scene.read_text(encoding="utf-8"))
            config["scene"] = scene
        if args.skills:
            config["skill_sources"] = [str(args.skills.resolve())]
            config["skills"] = [asdict(s) for s in load_skills(config["skill_sources"])]
        if args.fault:
            for backend in config["backends"].values():
                if backend["type"] == "mock":
                    backend["fault"] = args.fault
        service.config = AppConfig.model_validate(config)
        task_data = json.loads(args.task.read_text(encoding="utf-8")) if args.task else None
        instruction = task_data.get("instruction", "Structured task") if task_data else args.instruction
        created = await service.start(instruction, task_data)
        await service.worker
        record = service.store.get(created["id"])
        report = record["report"]
        args.log_dir.mkdir(parents=True, exist_ok=True)
        (args.log_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        events = service.runtime.log.events
        (args.log_dir / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        print(f"任务：{record['instruction']}\n规划：{service.config.planner.type}；后端：{service.config.observer_backend}")
        for i, result in enumerate(report.get("results", []), 1):
            print(f"{i:02d}. {result.get('skill', '')} → {result['execution']} / {result['effect']}")
        print(f"结果：{report['status']}；{report.get('reason', '')}\n日志：{args.log_dir}\n任务 ID：{created['id']}")
        return report
    finally:
        await service.close()


def main():
    parser = argparse.ArgumentParser(description="Primitive VLA — configurable embodied agent and web console")
    parser.add_argument("command", nargs="?", choices=("run", "serve", "init-config"), default="run")
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    parser.add_argument("--config", type=Path, help="Unified JSON; defaults to workspace/config.json")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--instruction", default=DEFAULT_INSTRUCTION)
    selection.add_argument("--task", type=Path)
    parser.add_argument("--scene", type=Path)
    parser.add_argument("--skills", type=Path, help="SKILL.md file or skill directory")
    parser.add_argument("--fault", choices=("none", "missed_grasp", "uncertain_pour", "timeout"))
    parser.add_argument("--log-dir", type=Path, help="Export directory; defaults to <workspace>/runs/<timestamp>")
    args = parser.parse_args()
    args.log_dir = args.log_dir or args.workspace / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    try:
        if args.command == "init-config":
            path = args.config or args.workspace / "config.json"
            if path.exists():
                parser.exit(2, f"配置已存在：{path}\n")
            save_config(default_config(), path)
            print(path)
            return
        if args.command == "serve":
            import uvicorn
            from primitive_vla.web.app import create_app
            config = load_config(args.config or args.workspace / "config.json")
            print(f"网页控制台：http://{config.web.host}:{config.web.port}")
            uvicorn.run(create_app(args.workspace, args.config), host=config.web.host, port=config.web.port)
            return
        report = asyncio.run(run(args))
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(2, f"启动或配置错误：{error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "任务已中断。\n")
    raise SystemExit(0 if report["status"] == "completed" else 1)
