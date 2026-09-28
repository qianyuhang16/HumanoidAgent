"""Task contracts and evidence checks, confined to this simulation extension."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, model_validator


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["target", "blue"]
    predicate: Literal["held", "placed", "unchanged"]
    zone: Literal["left", "right"] | None = None

    @model_validator(mode="after")
    def valid_zone(self):
        if (self.predicate == "placed") != (self.zone is not None):
            raise ValueError("Only placed criteria require a zone")
        return self


def task_goal(options):
    goal = options.get("goal")
    if goal is None:
        goal = ([{"target": "target", "predicate": "placed", "zone": "left"},
                 {"target": "blue", "predicate": "placed", "zone": "right"}]
                if options.get("scenario") == "sorting" else [{"target": "target", "predicate": "held"}])
    if not goal:
        raise ValueError("A task contract needs at least one criterion")
    return [Criterion.model_validate(item).model_dump(exclude_none=True) for item in goal]


class GoalBinding:
    """Interactive goals are declared once, before any physical command."""
    def __init__(self, options):
        self.interactive = options.get("interactive", False)
        self.criteria = [] if self.interactive else task_goal(options)

    def define(self, criteria):
        if not self.interactive or self.criteria:
            raise ValueError("The task contract is already fixed; start a new task to change it")
        validated = task_goal({"goal": criteria})
        self.criteria = validated
        return {"task_contract": validated, "source": "model_interpretation_of_user_instruction", "locked": True}

    def require_ready(self):
        if not self.criteria:
            raise ValueError("First use tabletop.set_goal to declare the user's goal; actions and verification need a task contract")


def evaluate_samples(goal, samples, required_steps=110):
    """Require ALL criteria simultaneously, consecutively, through the final sample."""
    runs = [0] * len(goal)
    joint_run = 0
    latest = [{} for _ in goal]
    for sample in samples:
        passing = []
        for i, criterion in enumerate(goal):
            state = sample.get(criterion["target"])
            if state is None:
                ok, evidence = False, {"present": False}
            elif criterion["predicate"] == "held":
                ok = state["bilateral_contacts"] and state["lift_m"] > .08
                evidence = {"present": True, "lift_m": state["lift_m"],
                            "bilateral_contacts": state["bilateral_contacts"]}
            elif criterion["predicate"] == "unchanged":
                ok = state["displacement_m"] <= .01 and state["rotation_error_rad"] <= .1 and not state["finger_contact"]
                evidence = {"present": True, **{key: state[key] for key in
                            ("displacement_m", "rotation_error_rad", "finger_contact")}}
            else:
                ok = (criterion["zone"] in state["inside_zones"] and state["table_contact"]
                      and not state["finger_contact"] and state["linear_speed_m_s"] < .03
                      and state["angular_speed_rad_s"] < .3)
                evidence = {"present": True, **{key: state[key] for key in
                            ("inside_zones", "table_contact", "finger_contact", "linear_speed_m_s", "angular_speed_rad_s")}}
            passing.append(bool(ok))
            runs[i] = runs[i] + 1 if ok else 0
            latest[i] = evidence
        joint_run = joint_run + 1 if all(passing) else 0
    return {"verdict": "satisfied" if joint_run >= required_steps else "unsatisfied",
            "success": joint_run >= required_steps, "joint_stable_steps": joint_run,
            "required_stable_steps": required_steps, "sample_count": len(samples),
            "criteria": [{**criterion, "satisfied": runs[i] >= required_steps,
                          "stable_steps": runs[i], "evidence": latest[i]} for i, criterion in enumerate(goal)]}


class TabletopFinishCheck:
    """Optional demo gate: only trusted, fresh tool evidence may certify completion."""
    def __init__(self, options, context):
        self.log = context.log

    async def check(self, answer, context):
        if answer.status != "completed":
            return None
        # Shared runtime log is authoritative and not affected by context truncation.
        events = self.log.events
        start = max((i for i, event in enumerate(events) if event["event"] == "task_started"), default=-1)
        verified = None
        for event in events[start + 1:]:
            call = event.get("call", {})
            if event["event"] == "tool_started" and call.get("name") in ("robot.move", "robot.gripper"):
                verified = None
            if event["event"] == "tool_finished" and call.get("name") == "tabletop.verify":
                result = event["result"]
                verified = call["id"] if (result["status"] == "ok" and result["data"].get("verdict") == "satisfied") else None
        if verified is None or verified not in answer.evidence:
            return "Completion needs a satisfied tabletop.verify for the configured task contract after the last action; cite its call ID. Tool execution or a plan is not goal evidence."
        return None
