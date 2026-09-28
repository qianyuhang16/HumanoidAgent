"""Select who judges action evidence; leave subsequent decisions to the Agent."""

import asyncio
from copy import deepcopy
from time import monotonic

from primitive_vla.tools import FunctionTool, ToolDefinition, ToolResult
from .contracts import OutcomeAssessment, OutcomeContext
from .images import paired_images
from .outcomes import Outcomes


class Verification:
    def __init__(self, session, definitions, log, reviewer=None):
        self.session, self.log, self.reviewer = session, log, reviewer
        self.definitions = {d.name: d for d in definitions}
        self.outcomes = Outcomes(session, log)
        session.verification_mode = "independent" if reviewer is not None else "main_agent"

    def validate(self, assessments):
        self.outcomes.validate(assessments, self.definitions)

    def accept(self, assessments):
        for assessment in assessments:
            result = self.outcomes.result(assessment.call_id)
            record = self.outcomes.track(result, self.definitions[result["tool"]].description)
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
        original = self.outcomes.result(record["call_id"])
        comparison = paired_images(record["call_id"], original.get("before_images", {}), original.get("images", {}))
        fresh = {f"{item['call_id']}:current:{name}": ref
                 for item in evidence for name, ref in item.get("images", {}).items()}
        images = {**fresh, **comparison}

        def without_images(item):
            return {k: deepcopy(v) for k, v in item.items() if k not in ("images", "before_images")}

        context = OutcomeContext(self.session.task, self.session.instructions,
            {"id": record["call_id"], "name": record["tool"], "arguments": deepcopy(record["arguments"])},
            self.definitions[record["tool"]].description, without_images(original),
            tuple(without_images(e) for e in evidence), images)
        self.log.write("outcome_review_started", call_id=record["call_id"], source="independent")
        try:
            if monotonic() >= deadline:
                raise TimeoutError("Task deadline reached before outcome assessment")
            assessment = OutcomeAssessment.model_validate(await asyncio.wait_for(
                self.reviewer.assess(context), timeout=max(.001, deadline - monotonic())))
            available = {record["call_id"], *images, *(e["call_id"] for e in evidence)}
            if not set(assessment.evidence) <= available:
                raise ValueError("Assessment evidence was not supplied to the reviewer")
        except asyncio.CancelledError:
            self.outcomes.publish(record, OutcomeAssessment(verdict="unknown",
                reason="Independent assessment cancelled; execution history retained"), "independent")
            raise
        except Exception as error:
            assessment = OutcomeAssessment(verdict="unknown",
                reason=f"Independent assessment unavailable: {type(error).__name__}: {error}")
        self.outcomes.publish(record, assessment, "independent")
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

        return [FunctionTool(ToolDefinition(name="session.assess", description=(
            "Ask the configured independent reviewer to reassess an action using recorded evidence call IDs. "
            "Optional, read-only; returns advice, never executes or unlocks actions."), parameters={
            "type": "object", "properties": {"call_id": {"type": "string", "minLength": 1},
            "evidence": {"type": "array", "items": {"type": "string", "minLength": 1},
                         "maxItems": 8, "uniqueItems": True}},
            "required": ["call_id", "evidence"], "additionalProperties": False}, timeout_s=120), assess)]
