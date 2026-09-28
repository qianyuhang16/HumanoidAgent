"""Configurable tools for the tabletop integration, using the public tool contract."""

from pathlib import Path
from time import monotonic, time
from uuid import uuid4
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from primitive_vla.runtime.process import JsonProcess
from primitive_vla.tools import FunctionTool, ToolDefinition, ToolResult
from .evaluation import Criterion, task_goal


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    python_executable: str
    robot_urdf: str
    models_dir: str
    seed: int = 7
    object_present: bool = True
    device: str = "cuda"
    depth_mode: Literal["rgbd", "depth_anything"] = "rgbd"
    box_threshold: float = Field(default=.25, ge=0, le=1)
    grasp_threshold: float = Field(default=.10, ge=0, le=1)
    scenario: Literal["clear", "sorting", "obstructed", "anchored", "empty"] = "clear"
    goal: list[Criterion] | None = Field(default=None, min_length=1)
    interactive: bool = False


def schema(**properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


class TabletopTools:
    def __init__(self, options, context):
        cfg = Options.model_validate(options)
        values = cfg.model_dump()
        values["goal"] = task_goal(values)
        self.log = context.log
        for key in ("robot_urdf", "models_dir"):
            path = Path(values[key]).expanduser()
            values[key] = str((context.config_dir / path).resolve() if not path.is_absolute() else path)
        self.process = JsonProcess(cfg.python_executable, "primitive_vla_tabletop.worker", [],
            context.workspace / "runs" / f"tabletop-{uuid4().hex}.log", timeout=180, startup_timeout=60,
            init_payload={"options": values, "media": str(context.workspace / "media")}, close_timeout=5,
            env={"HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"})
        string = {"type": "string", "minLength": 1}
        specs = [
            ("tabletop.observe", "observe", "Get fresh top-down RGB-D and overview images. Returns observation_id; no object poses are exposed.", schema(), True),
            ("perception.segment", "segment", "GroundingDINO + SAM identify the described target in an observation. Use English queries such as 'red block'. If found=false, do not move.", schema(observation_id=string, query=string), True),
            ("perception.depth", "depth", "Run Depth Anything V2 metric depth; report prediction error against RGB-D. Returns depth_ref and explicit source used for grasp geometry.", schema(observation_id=string), True),
            ("grasp.propose", "propose", "GG-CNN predicts grasp center, angle and width from the selected observation, SAM mask and depth. Returns candidate_id. All references must belong to the same fresh observation.", schema(observation_id=string, mask_ref=string, depth_ref=string), True),
            ("robot.plan_grasp", "plan", "Assess a perception-derived grasp WITHOUT moving: measured RGB-D approach clearance, workspace and endpoint IK. Returns verdict feasible/infeasible/unknown and checks. Only feasible results have plan_id. Feasible is a scoped prediction, NOT a guarantee of a grasp; attachment and full-arm collision are not assessed.", schema(candidate_id=string), True),
            ("robot.plan_place", "plan_place", "Plan transfer of the currently lifted object to a calibrated named pad. Uses the preceding grasp_plan_id, not object ground truth. Checks destination clearance from a fresh observation and endpoint IK. Returns feasibility and plan_id. Transfer, lower, OPEN gripper, retreat; then verify placement.", schema(grasp_plan_id=string, observation_id=string, zone={"type": "string", "enum": ["left", "right"]}), True),
            ("robot.move", "move", "Execute one waypoint of a feasible plan. Grasp: open, approach, descend, close, lift. Place: transfer, lower, open, retreat. Preconditions are enforced; choose plans and ordering yourself. Completion proves motion only.", schema(plan_id=string, waypoint={"type": "string", "enum": ["approach", "descend", "lift", "transfer", "lower", "retreat"]}), False),
            ("robot.gripper", "gripper", "Set parallel gripper width in metres: 0.08 opens, 0 closes. Motion completion does not prove a grasp.", schema(width_m={"type": "number", "minimum": 0, "maximum": .08}), False),
            ("tabletop.verify", "verify", "Evaluate ALL immutable task_contract criteria from observe using independent physical measurements over 120 steps. held requires bilateral contact and sustained lift; placed requires entire footprint inside the named pad, released, table-supported and settled; unchanged compares final position/orientation with the initial pose and requires no finger contact. Returns satisfied/unsatisfied per criterion and jointly. May be called before execution, between subgoals or at the end; fresh evidence required after subsequent actions. Never weakens the goal to the last action.", schema(), True),
        ]
        if cfg.interactive:
            criterion = {"type": "object", "properties": {
                "target": {"type": "string", "enum": ["target", "blue"]},
                "predicate": {"type": "string", "enum": ["held", "placed", "unchanged"]},
                "zone": {"type": "string", "enum": ["left", "right"]}},
                "required": ["target", "predicate"], "additionalProperties": False}
            specs.append(("tabletop.set_goal", "set_goal",
                "Translate the USER'S goal into immutable evaluation criteria before physical actions. target=red block, blue=blue block. held requires contact+lift, placed requires a zone (left/right), unchanged requires the final pose to remain within 1 cm and 0.1 rad of the initial pose with no finger contact (no zone). Use unchanged when the user requests another object remain in place. Define exactly the requested final conditions, never add unwanted objects or weaken them after failure. This tool may succeed only once per task. Unsupported goals must be reported blocked.",
                schema(criteria={"type": "array", "items": criterion, "minItems": 1, "maxItems": 8}), True))
        self.tools = []
        for name, operation, description, parameters, read_only in specs:
            async def call(args, call_context, op=operation):
                call_context.check_active()
                result = await self.process.request(op, arguments=args, call_id=call_context.call_id,
                    deadline=time() + max(0, call_context.deadline - monotonic()),
                    timeout=max(.001, call_context.deadline - monotonic()))
                images = result.pop("images", {})
                return ToolResult(data=result, images=images)
            self.tools.append(FunctionTool(ToolDefinition(name=name, description=description, parameters=parameters,
                read_only=read_only, resources=["tabletop"], timeout_s=180 if read_only else 60), call, self.stop))
        step = schema(id=string, description=string,
                      status={"type": "string", "enum": ["pending", "in_progress", "done", "blocked"]},
                      depends_on={"type": "array", "items": string},
                      evidence_call_ids={"type": "array", "items": string})
        self.tools.append(FunctionTool(ToolDefinition(name="task.plan",
            description="Publish or revise YOUR task decomposition, dependencies, status and brief rationale for the audience. Does not execute actions or prove feasibility/completion. Evidence IDs must be real completed tool calls. Keep the original task contract; update after new evidence, partial progress or a failed attempt. No prescribed number/order of subgoals.",
            parameters=schema(rationale=string, steps={"type": "array", "items": step, "minItems": 1, "maxItems": 20})), self.record_plan))

    async def record_plan(self, args, context):
        context.check_active()
        ids = [step["id"] for step in args["steps"]]
        if len(ids) != len(set(ids)):
            return ToolResult(status="rejected", text="Plan step IDs must be unique")
        start = max((i for i, e in enumerate(self.log.events) if e["event"] == "task_started"), default=-1)
        actual = {e["call"]["id"] for e in self.log.events[start + 1:] if e["event"] == "tool_finished"}
        graph = {step["id"]: step["depends_on"] for step in args["steps"]}
        for step in args["steps"]:
            if set(step["depends_on"]) - set(ids) or set(step["evidence_call_ids"]) - actual:
                return ToolResult(status="rejected", text="Unknown dependency or tool evidence ID")
        pending, resolved = set(ids), set()
        while pending:
            ready = {key for key in pending if set(graph[key]) <= resolved}
            if not ready:
                return ToolResult(status="rejected", text="Plan dependencies contain a cycle")
            pending -= ready
            resolved |= ready
        return ToolResult(data={**args, "source": "model_authored_plan", "is_execution_evidence": False})

    async def start(self):
        await self.process.start()

    async def stop(self, call_id, reason):
        result = await self.process.request("stop", call_id=call_id, reason=reason, timeout=5)
        return result.get("stopped") is True and result.get("call_id") == call_id

    async def close(self):
        await self.process.close()
