"""Shared evidence encoding, not a planner or a source of observed facts."""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json

from .li_task_effect import judge_case

# Declared synthetic tolerance, identical to the public sibling v4 synthetic setting
# (integrations/humanoid_continuation_v4/demo.py LI_BINDING["tolerance"]). Li's own rule
# is 2*sigma <= boundary_band_m with sigma = pose accuracy + cavity accuracy. The positive
# synthetic observation declares 0.004 + 0.005 = 0.009 m, so a 0.01 m band is (correctly)
# uncertain; 0.02 m is self-consistent. This is authored synthetic data, not a measured
# robot tolerance, and the Li algorithm itself is unchanged.
SYNTHETIC_TOLERANCE = {
    "boundary_band_m": 0.02, "inside_margin_m": 0.005, "coverage_required": 1.0,
    "basis": "authored synthetic geometry; same values as the sibling v4 synthetic demo "
             "(Li fixture 01 engineering tolerance); not a measured robot tolerance",
}


def stable(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def digest(value):
    return sha256(stable(value).encode()).hexdigest()


def check_resources(memory, refs):
    """Check every registration AND blob, including observation-only resources."""
    found = {}
    for ref in refs:
        value = asdict(ref) if not isinstance(ref, dict) else ref
        rid = value["id"]
        if rid in found:
            if found[rid] != value:
                raise ValueError("conflicting resource references: " + rid)
            continue
        if asdict(memory.get_resource(rid)) != value:
            raise ValueError("resource registration differs: " + rid)
        raw = memory.read_resource(rid)
        if value["uri"] != "blob:" + sha256(raw).hexdigest():
            raise ValueError("resource bytes differ: " + rid)
        found[rid] = value
    return found


def judge(observation):
    """Map synthetic blob URIs explicitly for the unchanged Li synthetic-case ABI.

    The native case retains blob URIs. Only this calculation copy uses synthetic URIs;
    this mapping is not physical evidence and is never written over a native resource.
    """
    case = deepcopy(observation["case"])
    if case["origin"] != "synthetic":
        raise ValueError("this example accepts only declared synthetic observations")
    for ref in case["resources"]:
        ref["uri"] = "synthetic://chen-taskaware/" + ref["id"]
    return judge_case(case)


def make_context(task, goal, identity, ended_at, call_id):
    return {
        "schema_version": "t05.task_context.v1",
        "task_id": identity["task_id"], "session_id": identity["session_id"],
        "scene_version": identity["scene_revision"], "map_version": task["map_version"],
        "coordinate_frame": "synthetic_box_frame", "length_unit": "m",
        "identity_scope": task["task_id"], "goal": task["instruction"],
        "box_object_id": goal["container_id"],
        "items": {g["li_item"]: g["object_id"] for g in task["goals"]},
        "action": {"call_id": call_id, "action": "place", "item": goal["li_item"],
                   "object_id": goal["object_id"], "tool_status": "completed",
                   "visit": goal["visit"], "attempt": identity["attempt"],
                   "end_time": ended_at},
        "tolerance": deepcopy(SYNTHETIC_TOLERANCE),
    }
