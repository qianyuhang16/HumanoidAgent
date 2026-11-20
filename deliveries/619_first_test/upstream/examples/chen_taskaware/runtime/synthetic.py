"""Synthetic external interfaces. No scripted decisions, model, SDK or robot transport."""
from copy import deepcopy
from dataclasses import asdict
import json
import time

from primitive_vla.memory.files import write_json
from primitive_vla.tools.contracts import ToolResult, Verification

from legacy.evidence import check_resources, digest, judge, make_context, stable

# Declared synthetic object-pose accuracy per scenario. "positive" is self-consistent with
# SYNTHETIC_TOLERANCE (2*(0.004+0.005)=0.018 <= 0.02). "uncertain_precision" is an explicit
# degraded observation (2*(0.008+0.005)=0.026 > 0.02) for which Li must answer uncertain.
SCENARIOS = {"positive": 0.004, "uncertain_precision": 0.008}


class Environment:
    is_test_double = True

    def __init__(self, memory, task, scenario="positive"):
        if scenario not in SCENARIOS:
            raise ValueError("unknown synthetic scenario: " + str(scenario))
        self.memory, self.task = memory, deepcopy(task)
        self.path = memory.directory / "synthetic_world.json"
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            if self.state["task_sha256"] != digest(task):
                raise ValueError("platform task changed on reopen")
            if self.state["scenario"] != scenario:
                raise ValueError("synthetic scenario changed on reopen")
            if self.state["pending_call"] is not None:
                raise ValueError("platform has an unfinished call; automatic replay refused")
        else:
            self.state = {
                "task_sha256": digest(task), "scenario": scenario,
                "pending_call": None, "owner": None,
                "positions": {g["object_id"]: [0.7, 0.25, 0.08] for g in task["goals"]},
                "executions": {}, "observations": {}, "ended_calls": [], "place_count": 0,
            }
            write_json(self.path, self.state)

    def close(self):
        if self.state["pending_call"] is not None:
            raise RuntimeError("synthetic operation termination is unconfirmed")

    def resource(self, value, description):
        return self.memory.put_resource(stable(value).encode(), "application/json", description)

    def observe(self, arguments, identity, goal):
        execution = self.state["executions"][arguments["related_call_id"]]
        if execution["identity"] != identity:
            raise ValueError("observation refers to a different action identity/revision")
        captured = time.time()
        if captured <= execution["ended_at"]:
            raise ValueError("observation must follow the completed action")
        box = goal["container_id"]
        payloads = [
            {"kind": "cavity", "box_object_id": box, "cavity_measurement": "direct",
             "method": "depth_interior_scan", "footprint": {"min": [0.1, 0.1], "max": [0.4, 0.4]},
             "z_range": [0.02, 0.15],
             "full_bbox": {"min": [0.0, 0.0, 0.0], "max": [0.5, 0.5, 0.18]},
             "accuracy_m": 0.005},
            {"kind": "object_pose", "object_id": goal["object_id"],
             "center": self.state["positions"][goal["object_id"]],
             "half_extent": [0.02, 0.02, 0.02],
             "accuracy_m": SCENARIOS[self.state["scenario"]]},
            {"kind": "coverage", "box_object_id": box, "region": "box_cavity",
             "covered_fraction": 1.0, "accuracy_m": 0.01},
        ]
        ctx = make_context(self.task, goal, identity, execution["ended_at"],
                           arguments["related_call_id"])
        observations, refs = [], []
        for payload, source in zip(payloads, ("cavity_sensing", "instance_geometry", "coverage")):
            ref = self.resource({"synthetic": True, "captured_at": captured, "payload": payload},
                                "Synthetic " + payload["kind"])
            refs.append(ref)
            observations.append({
                **{k: ctx[k] for k in ("scene_version", "map_version", "coordinate_frame",
                                       "length_unit", "identity_scope", "session_id", "task_id")},
                "observation_id": ref.id, "source_kind": source, "captured_at": captured,
                "related_call_id": arguments["related_call_id"], "visit": goal["visit"],
                "attempt": identity["attempt"], "payload": payload, "resource_refs": [ref.id],
            })
        return {"identity": identity, "synthetic": True,
                "case": {"schema_version": "t05.effect_case.v1", "origin": "synthetic",
                         "task_context": ctx, "resources": [asdict(r) for r in refs],
                         "observations": observations}}, tuple(refs)


class SyntheticTool:
    is_test_double = True

    def __init__(self, environment, spec, options):
        self.env, self.operation = environment, options["operation"]

    async def execute(self, arguments, context):
        env = self.env
        owner = [context.session_id, context.task_id]
        if env.state["owner"] not in (None, owner):
            raise ValueError("platform is already owned by another framework session/task")
        env.state["owner"] = owner
        goal = next(g for g in env.task["goals"] if g["goal_id"] == arguments["goal_id"])
        identity = {k: arguments[k] for k in
                    ("external_task_id", "scene_revision", "goal_id", "visit", "attempt")}
        identity.update(session_id=context.session_id, task_id=context.task_id)
        if (identity["external_task_id"] != env.task["task_id"]
                or identity["visit"] != goal["visit"]):
            raise ValueError("synthetic provider identity mismatch")
        if self.operation in ("navigate", "place"):
            if env.state["pending_call"] is not None:
                raise ValueError("another synthetic execution has not ended")
            env.state["pending_call"] = context.call_id
            write_json(env.path, env.state)
            # A crash after this marker is quarantined on reopen, never silently replayed.
        refs, verification = (), None
        if self.operation == "place":
            env.state["positions"][goal["object_id"]] = [0.25, 0.25, 0.08]
            env.state["place_count"] += 1
            payload = {"identity": identity, "synthetic": True, "ended_at": time.time()}
            env.state["executions"][context.call_id] = deepcopy(payload)
            refs = (env.resource(payload, "Synthetic execution receipt, not an effect"),)
        elif self.operation == "navigate":
            env.state["robot_near"] = goal["object_id"]
            payload = {"identity": identity, "synthetic": True, "ended_at": time.time()}
        elif self.operation == "observe":
            payload, refs = env.observe(arguments, identity, goal)
            env.state["observations"][context.call_id] = deepcopy(payload)
        elif self.operation == "verify":
            observation = env.state["observations"][arguments["observation_call_id"]]
            if (observation["identity"] != identity
                    or observation["case"]["task_context"]["action"]["call_id"]
                    != arguments["related_call_id"]):
                raise ValueError("Verification observation belongs to another call")
            table = check_resources(env.memory, observation["case"]["resources"])
            verdict = judge(observation)
            if verdict["status"] != "ok":
                raise ValueError("Li rejected synthetic evidence: " + verdict["reason"])
            payload = {"identity": identity, "synthetic": True, "verdict": verdict,
                       "observation_call_id": arguments["observation_call_id"]}
            # The cavity is intentionally observation-only in the native record, so the
            # retained-proof gate must re-read ALL observation resources, not just these.
            pose_id = observation["case"]["observations"][1]["resource_refs"][0]
            refs = (env.memory.get_resource(table[pose_id]["id"]),)
            verification = Verification(
                goal["subject"], verdict["conclusion"], verdict["reason"], refs,
                arguments["related_call_id"], verdict["failure_type"],
            )
        else:
            raise ValueError("unsupported synthetic operation")
        env.state["ended_calls"].append(context.call_id)
        env.state["pending_call"] = None
        write_json(env.path, env.state)
        return ToolResult(payload, refs, verification)

    async def cancel(self, context):
        return context.call_id in self.env.state["ended_calls"]


def factory(context, spec, options):
    """Called by the framework loader; names and schemas come solely from skill.md."""
    return SyntheticTool(context, spec, options)
