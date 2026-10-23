"""Select who judges action evidence; leave subsequent decisions to the Agent."""

import asyncio
from copy import deepcopy
from time import monotonic

from primitive_vla.tools import FunctionTool, ToolDefinition, ToolResult
from .contracts import OutcomeAssessment, OutcomeContext, AssessmentResult, VerificationPolicy
from .policy import GoalPolicy
from .images import paired_images
from .outcomes import Outcomes


class Verification:
    def __init__(self, session, definitions, log, reviewer=None, policy=None):
        self.session, self.log, self.reviewer = session, log, reviewer
        self.policy = GoalPolicy(session.state, policy or VerificationPolicy())
        session.verification_requirements = self.policy.config.model_dump(exclude_defaults=True)
        self.definitions = {d.name: d for d in definitions}
        self.outcomes = Outcomes(session, log)
        session.verification_mode = "independent" if reviewer is not None else "main_agent"

    def validate(self, assessments):
        self.outcomes.validate(assessments, self.definitions, self.policy.config.goals)

    def accept(self, assessments):
        for assessment in assessments:
            result = self.outcomes.result(assessment.call_id)
            goal = (self.policy.config.goals[assessment.goal_id].description if assessment.goal_id
                    else self.definitions[result["tool"]].description)
            record = self.outcomes.track(result, goal, assessment.goal_id)
            self.outcomes.publish(record, assessment, "main_agent")

    async def after_action(self, call, result, deadline):
        definition = self.definitions.get(call.name)
        if definition is None or not definition.review_outcome or self.outcomes.find(call.id):
            return
        if (result.status != "ok" and not result.before_images) or not result.control_released:
            return
        record = self.outcomes.track(self.outcomes.result(call.id), definition.description)
        if self.reviewer is not None:
            await self.assess(record, [], deadline)

    async def assess(self, record, evidence, deadline):
        original = self.outcomes.result(record["call_id"]) or {}
        comparison = paired_images(record["call_id"], original.get("before_images", {}), original.get("images", {}))
        fresh = {f"{item['call_id']}:current:{name}": ref
                 for item in evidence for name, ref in item.get("images", {}).items()}
        images = {**fresh, **comparison}

        def without_images(item):
            return {k: deepcopy(v) for k, v in item.items() if k not in ("images", "before_images")}

        context = OutcomeContext(self.session.task, self.session.instructions,
            {"id": record["call_id"], "name": record["tool"], "arguments": deepcopy(record["arguments"])},
            self.definitions[record["tool"]].description if record["tool"] else "Goal assessment",
            without_images(original), tuple(without_images(e) for e in evidence), images,
            record.get("goal_id"), record["goal"])
        self.log.write("outcome_review_started", call_id=record["call_id"], source="independent")
        run_status = "ok"
        try:
            if monotonic() >= deadline:
                raise TimeoutError("Task deadline reached before outcome assessment")
            reply = await asyncio.wait_for(self.reviewer.assess(context), timeout=max(.001, deadline - monotonic()))
            if isinstance(reply, AssessmentResult) or isinstance(reply, dict) and "status" in reply:
                response = AssessmentResult.model_validate(reply)
                run_status = response.status
                assessment = response.assessment or OutcomeAssessment(verdict="unknown", reason=response.reason or response.status)
            else:
                assessment = OutcomeAssessment.model_validate(reply)
            available = {record["call_id"], *images, *(e["call_id"] for e in evidence)}
            available.update(o["id"] for item in [original, *evidence] for o in item.get("observations", []))
            if not set(assessment.evidence) <= available:
                raise ValueError("Assessment evidence was not supplied to the reviewer")
            if assessment.goal_id not in (None, record.get("goal_id")):
                raise ValueError("Reviewer cannot change the requested goal")
            assessment = assessment.model_copy(update={"goal_id": record.get("goal_id"),
                "evidence": [ref.replace(":current:", ":", 1) for ref in assessment.evidence]})
        except asyncio.CancelledError:
            self.outcomes.publish(record, OutcomeAssessment(verdict="unknown",
                reason="Independent assessment cancelled; execution history retained"), "independent", "error")
            raise
        except Exception as error:
            run_status = "error"
            assessment = OutcomeAssessment(verdict="unknown",
                reason=f"Independent assessment unavailable: {type(error).__name__}: {error}")
        self.outcomes.publish(record, assessment, "independent", run_status)
        return assessment

    def tools(self):
        if self.reviewer is None:
            return []

        async def assess(args, context):
            record = self.outcomes.find(args["call_id"])
            if record is None or record["tool"] not in self.definitions:
                return ToolResult(status="rejected", text="Unknown action outcome")
            evidence = [self.outcomes.result(call_id) for call_id in args["evidence"]]
            if any(item is None for item in evidence):
                return ToolResult(status="rejected", text="Evidence must reference recorded tool calls")
            assessment = await self.assess(record, evidence, context.deadline)
            return ToolResult(data={"call_id": record["call_id"], "source": "independent", **assessment.model_dump()})

        async def assess_goal(args, context):
            goal_id = args["goal_id"]
            goal = self.policy.config.goals.get(goal_id)
            if goal is None:
                return ToolResult(status="rejected", text="Unknown goal")
            evidence = [self.outcomes.result(ref) for ref in args["evidence"]]
            if any(item is None for item in evidence):
                return ToolResult(status="rejected", text="Evidence must reference recorded tool calls")
            record = self.outcomes.track(None, goal.description, goal_id)
            assessment = await self.assess(record, evidence, context.deadline)
            return ToolResult(data={"goal_id": goal_id, "run_status": record["run_status"], **assessment.model_dump()})

        tools = [FunctionTool(ToolDefinition(name="session.assess", description=(
            "Ask the configured independent reviewer to reassess an action using recorded evidence call IDs. "
            "Optional, read-only; returns advice, never executes or unlocks actions."), parameters={
            "type": "object", "properties": {"call_id": {"type": "string", "minLength": 1},
            "evidence": {"type": "array", "items": {"type": "string", "minLength": 1},
                         "maxItems": 8, "uniqueItems": True}},
            "required": ["call_id", "evidence"], "additionalProperties": False}, timeout_s=120), assess)]
        if self.policy.config.goals:
            tools.append(FunctionTool(ToolDefinition(name="verification.assess", description=(
                "Assess a declared goal with the configured reviewer using evidence call IDs."), parameters={
                "type": "object", "properties": {"goal_id": {"type": "string", "enum": list(self.policy.config.goals)},
                "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8}},
                "required": ["goal_id", "evidence"], "additionalProperties": False}, timeout_s=120), assess_goal))
        return tools
