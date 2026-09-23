"""Command-line entry points sharing the application task service."""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from primitive_vla.config.loader import default_config, load_config, save_config
from primitive_vla.runtime.service import TaskService


async def run(args):
    service = TaskService(args.workspace, args.config)
    try:
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
        print(f"任务：{record['instruction']}\n模型：{service.config.model.adapter}")
        for i, result in enumerate(report.get("results", []), 1):
            print(f"{i:02d}. {result.get('tool', '')} → {result['status']} {result.get('text', '')}")
        print(f"结果：{report['status']}；{report.get('reason', '')}\n日志：{args.log_dir}\n任务 ID：{created['id']}")
        return report
    finally:
        await service.close()


def main():
    parser = argparse.ArgumentParser(description="Primitive VLA — configurable embodied agent and web console")
    parser.add_argument("command", nargs="?", choices=("run", "serve", "init-config"), default="run")
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    parser.add_argument("--config", type=Path, help="Unified YAML; defaults to workspace/config.yaml")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Load local environment values without overriding exported variables")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--instruction", default="观察当前环境，报告可用能力和当前状态。")
    selection.add_argument("--task", type=Path)
    parser.add_argument("--log-dir", type=Path, help="Export directory; defaults to <workspace>/runs/<timestamp>")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    args.log_dir = args.log_dir or args.workspace / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    try:
        if args.command == "init-config":
            path = args.config or args.workspace / "config.yaml"
            if path.exists():
                parser.exit(2, f"配置已存在：{path}\n")
            save_config(default_config(), path)
            print(path)
            return
        if args.command == "serve":
            import uvicorn
            from primitive_vla.web.app import create_app
            config_path = args.config or args.workspace / "config.yaml"
            config = load_config(config_path) if config_path.exists() else default_config()
            print(f"网页控制台：http://{config.web.host}:{config.web.port}")
            uvicorn.run(create_app(args.workspace, args.config), host=config.web.host, port=config.web.port)
            return
        report = asyncio.run(run(args))
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(2, f"启动或配置错误：{error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "任务已中断。\n")
    raise SystemExit(0 if report["status"] == "completed" else 1)
