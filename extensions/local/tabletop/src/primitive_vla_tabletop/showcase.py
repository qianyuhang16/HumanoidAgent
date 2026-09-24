"""Run a real tool-calling model and export its plans, assessments and evidence."""

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv
from primitive_vla.config.loader import load_config
from primitive_vla.runtime.session import Runtime
from primitive_vla.storage.events import EventLog
from .demo import write_report


TASKS = {
    "clear": "观察桌面，抓起红色方块，并确认它被夹爪稳定夹住。展示计划、可行性依据和最终验收。",
    "sorting": "把红色方块放到 left 绿色区域，把蓝色方块放到 right 黄色区域；最终两个方块都要完全位于各自区域内，松开且稳定。请展示计划、可行性判断、部分进度和最终验证。",
    "obstructed": "请抓起红色方块并稳定夹住。先判断计划是否可行，遇到障碍时报告具体依据，不要盲目尝试。",
    "anchored": "做一次红色方块抓取演示：先展示计划并判断可行性，再尝试抓起并稳定夹住，最后验证物体是否真的被抬起。本次只允许一次抓取尝试；如果验收未通过，请更新计划状态、引用测量依据报告失败并结束，不要重复抓取或继续定位。",
    "empty": "请抓起红色方块并稳定夹住。先观察是否存在目标，没有足够证据时报告阻塞。",
}


async def run(args):
    config = load_config(args.config)
    config.tool_providers[0].options.update(scenario=args.case, goal=None)
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    # Resolve paths so the config snapshot remains usable from the run directory.
    saved = config.model_copy(deep=True)
    directory = args.config.resolve().parent
    for key in ("robot_urdf", "models_dir"):
        value = Path(saved.tool_providers[0].options[key]).expanduser()
        saved.tool_providers[0].options[key] = str((directory / value).resolve() if not value.is_absolute() else value)
    saved.instructions = [str((directory / path).resolve()) for path in saved.instructions]
    (workspace / "run-config.json").write_text(saved.model_dump_json(indent=2), encoding="utf-8")
    def progress(event):
        if event["event"] == "tool_finished":
            result = event["result"]
            print(f'{event["call"]["name"]}: {result["status"]} {result["data"].get("verdict", "")}', flush=True)
        elif event["event"] == "model_failed":
            print(f'Model feedback: {event["reason"]}', flush=True)
    log = EventLog(workspace / "events.jsonl", sink=progress)
    runtime = Runtime(config, workspace, log, config_dir=args.config.resolve().parent)
    instruction = args.instruction or TASKS[args.case]
    report = {"mode": "agent", "case": args.case, "instruction": instruction,
              "status": "failed", "reason": "", "calls": []}
    try:
        result = await runtime.run(instruction)
        report.update(status=result["status"], reason=result["reason"], evidence=result["evidence"])
    except asyncio.CancelledError:
        report.update(status="cancelled", reason="演示被中断；只保留已发生的调用和测量，不宣称任务完成。")
        raise
    except Exception as error:
        report["reason"] = f"{type(error).__name__}: {error}"
    finally:
        try:
            await runtime.close()
        finally:
            report["calls"] = [{"tool": event["call"]["name"], "call_id": event["call"]["id"],
                                "arguments": event["call"]["arguments"], "result": event["result"]}
                               for event in log.events if event["event"] == "tool_finished"]
            (workspace / "session.json").write_text(json.dumps(runtime.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
            write_report(workspace, report)
    print(f'{report["status"]}: {report["reason"]}\nReport: {workspace / "report.html"}')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[2] / "configs/tabletop.yaml")
    parser.add_argument("--case", choices=TASKS, default="sorting")
    parser.add_argument("--workspace", type=Path, default=Path("workspace/tabletop-showcase"))
    parser.add_argument("--instruction", help="Must agree with this case's evaluation goal; choose/customize config for other goals")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    report = asyncio.run(run(args))
    raise SystemExit(0 if report["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
