"""Run the actual tabletop tools in a scripted integration check, without an LLM."""

import argparse
import asyncio
import html
import json
from pathlib import Path

from primitive_vla.config.loader import load_config
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import PluginContext, ToolCall
from primitive_vla.tools.runtime import ToolRuntime
from .provider import TabletopTools


def write_report(workspace, report):
    (workspace / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    cards = []
    for entry in report["calls"]:
        result = entry["result"]
        pictures = "".join(f'<figure><img src="media/{html.escape(ref, quote=True)}"><figcaption>{html.escape(label)}</figcaption></figure>'
                           for label, ref in result["images"].items())
        details = html.escape(json.dumps(result["data"], ensure_ascii=False, indent=2))
        name, data = entry["tool"], result["data"]
        explanation = ""
        if name == "task.plan":
            rows = ''.join(f'<li><b>{html.escape(step["id"])} · {html.escape(step["status"])}</b> '
                           f'{html.escape(step["description"])}<small>依赖：{html.escape(", ".join(step["depends_on"]) or "无")} '
                           f'· 依据：{html.escape(", ".join(step["evidence_call_ids"]) or "待收集")}</small></li>' for step in data.get("steps", []))
            explanation = f'<p>{html.escape(data.get("rationale", ""))}</p><ol>{rows}</ol>'
        elif name in ("robot.plan_grasp", "robot.plan_place"):
            explanation = f'<p class="verdict">可行性：{html.escape(data.get("verdict", result["status"]))}</p><p>执行前预测，不能证明目标完成。{html.escape(data.get("reason", ""))}</p>'
        elif name == "tabletop.verify":
            rows = ''.join(f'<li>{"✓" if c["satisfied"] else "✗"} {html.escape(c["target"])} / {html.escape(c["predicate"])} '
                           f'{html.escape(c.get("zone", ""))} · 连续通过 {c["stable_steps"]} 步</li>' for c in data.get("criteria", []))
            explanation = f'<p class="verdict">目标验收：{html.escape(data.get("verdict", "unknown"))}</p><ul>{rows}</ul>'
        stage = "plan" if name == "task.plan" else "feasibility" if name in ("robot.plan_grasp", "robot.plan_place") else "verification" if name == "tabletop.verify" else "execution"
        cards.append(f'<article data-stage="{stage}"><h2>{html.escape(name)} · {html.escape(result["status"])}</h2><small>证据 ID：{html.escape(entry.get("call_id", ""))}</small>{explanation}{pictures}'
                     f'<details><summary>参数、测量与原始反馈</summary><pre>{html.escape(json.dumps(entry["arguments"], ensure_ascii=False, indent=2))}\n{details}\n{html.escape(result.get("text", ""))}</pre></details></article>')
    videos = [entry["result"]["data"]["video"] for entry in report["calls"] if "video" in entry["result"]["data"]]
    movie = f'<video controls preload="metadata" src="media/{html.escape(videos[-1], quote=True)}"></video>' if videos else ""
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Tabletop · 计划与验证</title>
<style>body{{font:16px/1.6 system-ui;background:#101923;color:#e7edf4;max-width:1080px;margin:40px auto;padding:0 20px}}
article{{background:#1b2938;padding:20px;margin:20px 0;border-radius:12px}}figure{{display:inline-block;margin:8px;max-width:46%}}
img,video{{max-width:100%;border-radius:8px}}video{{width:800px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}h1{{font-size:28px}}small{{display:block;color:#b5c6d6}}.verdict{{font-weight:bold;color:#7de2d1}}li{{margin:10px 0}}nav{{position:sticky;top:0;background:#101923;padding:12px 0;z-index:1}}button{{background:#243b50;color:white;border:1px solid #486179;padding:9px 16px;cursor:pointer;border-radius:6px;margin:4px}}button[aria-pressed="true"]{{background:#146a65}}article[hidden]{{display:none}}.reason{{white-space:pre-wrap}}</style>
<h1>Tabletop · 计划与验证 · {html.escape(report["status"])}</h1><details><summary>最终报告</summary><p class="reason">{html.escape(report["reason"])}</p></details>
<p>{"Agent 模式：使用配置中的决策模型。以下仅展示实际返回的计划、工具调用和测量记录。" if report["mode"] == "agent" else "脚本集成验证：调用真实感知模型和物理仿真，但调用顺序由测试代码指定，不代表 Agent 的规划能力。"}</p>
<p>任务：{html.escape(report.get("instruction", ""))}</p><p>几何使用配置指定深度；物体真实位姿仅用于独立验收。可行性是有限范围的预检，最终验收同时检查所有目标。</p>
<p>计划记录 {sum(c["tool"] == "task.plan" for c in report["calls"])} 次 · 可行性预检 {sum(c["tool"] in ("robot.plan_grasp", "robot.plan_place") for c in report["calls"])} 次 · 目标验收 {sum(c["tool"] == "tabletop.verify" for c in report["calls"])} 次</p>{movie}
<nav aria-label="展示内容"><button data-filter="story" aria-pressed="true">关键决策</button><button data-filter="plan">计划拆分</button><button data-filter="feasibility">可行性</button><button data-filter="verification">目标验证</button><button data-filter="all">完整工具记录</button></nav>
{''.join(cards)}<script>
function filter(stage) {{
  document.querySelectorAll('article').forEach(card => card.hidden = stage === 'story' ? card.dataset.stage === 'execution' : stage !== 'all' && card.dataset.stage !== stage);
  document.querySelectorAll('button[data-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.filter === stage)));
}}
document.querySelectorAll('button[data-filter]').forEach(button => button.addEventListener('click', () => filter(button.dataset.filter)));
filter('story');
</script></html>'''
    (workspace / "report.html").write_text(page, encoding="utf-8")


async def run(args):
    config = load_config(args.config)
    options = dict(config.tool_providers[0].options)
    if getattr(args, "scenario", None):
        options["scenario"] = args.scenario
        options["goal"] = None
    if args.seed is not None:
        options["seed"] = args.seed
    if args.empty:
        options["object_present"] = False
    if args.depth_mode:
        options["depth_mode"] = args.depth_mode
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    log = EventLog(workspace / "events.jsonl")
    provider = TabletopTools(options, PluginContext(workspace, args.config.resolve().parent, log))
    runtime = ToolRuntime(provider.tools, log=log, stop_timeout_s=8)
    report = {"mode": "scripted_tool_integration_no_llm", "seed": options.get("seed", 7),
              "scenario": options.get("scenario", "clear"), "instruction": "配置中的全部目标（固定脚本测试）",
              "object_present": options.get("object_present", True), "depth_mode": options.get("depth_mode", "rgbd"),
              "status": "failed", "reason": "", "calls": []}

    async def call(name, **arguments):
        request = ToolCall(name=name, arguments=arguments)
        result = await runtime.invoke(request)
        report["calls"].append({"tool": name, "call_id": request.id, "arguments": arguments, "result": result.model_dump()})
        print(f"{name}: {result.status} {json.dumps(result.data, ensure_ascii=False)} {result.text}", flush=True)
        if result.status != "ok":
            raise RuntimeError(f"{name}: {result.text}")
        return result.data

    try:
        await provider.start()
        await call("tabletop.verify")
        criteria = provider.process.init_payload["options"]["goal"]
        for criterion in criteria:
            await perform(criterion, call, args.query)
            verification = await call("tabletop.verify")
        report.update(status="completed" if verification["success"] else "failed",
                      reason="全部目标通过持续物理验收。" if verification["success"] else "动作结束，但目标验收未通过；不能宣称完成。")
    except Blocked as error:
        report.update(status="blocked", reason=str(error))
    except Exception as error:
        report["reason"] = str(error)
    finally:
        await runtime.close()
        await provider.close()
        write_report(workspace, report)
    print(f"Report: {workspace / 'report.html'}", flush=True)
    return report


class Blocked(RuntimeError):
    pass


async def perform(criterion, call, query):
    # A deterministic integration fixture only; the Agent never imports this recipe.
    observation = await call("tabletop.observe")
    refs = {"observation_id": observation["observation_id"]}
    segmentation = await call("perception.segment", **refs, query=query if criterion["target"] == "target" else "blue block")
    if not segmentation["found"]:
        raise Blocked("未检测到明确目标；该目标未发送运动或夹爪指令。")
    else:
        depth = await call("perception.depth", **refs)
        grasp = await call("grasp.propose", **refs, mask_ref=segmentation["mask_ref"], depth_ref=depth["depth_ref"])
        plan = await call("robot.plan_grasp", candidate_id=grasp["candidate_id"])
        if plan["verdict"] != "feasible":
            raise Blocked(f"抓取预检未通过：{plan}")
        await call("robot.gripper", width_m=.08)
        await call("robot.move", plan_id=plan["plan_id"], waypoint="approach")
        await call("robot.move", plan_id=plan["plan_id"], waypoint="descend")
        await call("robot.gripper", width_m=0.)
        await call("robot.move", plan_id=plan["plan_id"], waypoint="lift")
        if criterion["predicate"] == "placed":
            observation = await call("tabletop.observe")
            placement = await call("robot.plan_place", grasp_plan_id=plan["plan_id"],
                                   observation_id=observation["observation_id"], zone=criterion["zone"])
            if placement["verdict"] != "feasible":
                raise Blocked(f"放置预检未通过：{placement}")
            await call("robot.move", plan_id=placement["plan_id"], waypoint="transfer")
            await call("robot.move", plan_id=placement["plan_id"], waypoint="lower")
            await call("robot.gripper", width_m=.08)
            await call("robot.move", plan_id=placement["plan_id"], waypoint="retreat")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Path to the extension's scene configuration")
    parser.add_argument("--workspace", type=Path, default=Path("workspace/tabletop-demo"))
    parser.add_argument("--query", default="red block")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--empty", action="store_true")
    parser.add_argument("--scenario", choices=("clear", "sorting", "obstructed", "anchored", "empty"))
    parser.add_argument("--depth-mode", choices=("rgbd", "depth_anything"))
    args = parser.parse_args()
    report = asyncio.run(run(args))
    raise SystemExit(0 if report["status"] == "completed" or (args.empty and report["status"] == "blocked") else 1)


if __name__ == "__main__":
    main()
