import unittest
from time import monotonic

from primitive_vla.agent.contracts import FinalAnswer
from primitive_vla.agent.memory import Session
from primitive_vla.execution.runtime import ExecutionManager
from primitive_vla.state.contracts import Observation
from primitive_vla.state.contracts import StateClaim
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import FunctionTool, ToolCall, ToolDefinition, ToolResult
from primitive_vla.verification.contracts import ActionAssessment, AssessmentResult, GoalSpec, VerificationPolicy
from primitive_vla.verification.runtime import Verification


class VerificationPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 100.0
        self.session = Session()
        self.session.state.clock = lambda: self.now
        self.policy = VerificationPolicy(goals={"held": GoalSpec(description="Cup is held", subject="cup", max_age_s=5.0)},
                                         before_tools={"place": ["held"]}, completion=["held"])
        self.verification = Verification(self.session, [], EventLog(), policy=self.policy)

    def observe(self, ref, captured=100.0):
        self.session.record_result(ToolCall(id=ref + "-call", name="camera"), ToolResult(
            observations=[Observation(id=ref, sensor="camera", captured_at=captured)]))

    def assess(self, ref, verdict="satisfied", source="main_agent"):
        assessment = ActionAssessment(goal_id="held", verdict=verdict, reason="Observed cup", evidence=[ref])
        self.verification.validate([assessment])
        record = self.verification.outcomes.track(None, "Cup is held", "held")
        self.verification.outcomes.publish(record, assessment, source)

    async def test_requirement_is_enforced_at_dispatch_and_invalidated_by_motion(self):
        dispatched = []
        async def call(args, context):
            dispatched.append(context.call_id)
            return ToolResult()
        async def stop(*args):
            return True
        runtime = ExecutionManager([FunctionTool(ToolDefinition(name="place", description="Place cup",
            read_only=False, resources=["arm"]), call, stop)], state=self.session.state,
            precondition=self.verification.policy.before_call)
        self.assertEqual((await runtime.invoke(ToolCall(name="place"))).status, "rejected")
        self.observe("obs")
        self.assess("obs")
        self.assertEqual((await runtime.invoke(ToolCall(name="place"))).status, "ok")
        self.assertEqual((await runtime.invoke(ToolCall(name="place"))).status, "rejected")
        self.assertEqual(len(dispatched), 1)

    async def test_conflicts_staleness_and_unknown_revoke_completion(self):
        final = FinalAnswer(status="completed", reason="done")
        self.observe("obs")
        self.assess("obs")
        self.assertIsNone(self.verification.policy.finish(final))
        self.assess("obs", "unsatisfied", "independent")
        self.assertIsNotNone(self.verification.policy.finish(final))
        self.assess("obs", "satisfied", "independent")
        self.now = 106.0
        self.assertIsNotNone(self.verification.policy.finish(final))

    async def test_late_old_assessment_and_historical_evidence_do_not_advance(self):
        self.observe("new", 99.0)
        self.assess("new", "unsatisfied")
        self.observe("old", 98.0)
        self.assess("old", "satisfied")
        self.assertIsNotNone(self.verification.policy.check(["held"]))
        self.session.state.restore(self.session.state.snapshot())
        self.assertIsNotNone(self.verification.policy.check(["held"]))

    async def test_unsupported_is_not_semantic_failure_or_silent_fallback(self):
        class Reviewer:
            async def assess(self, context):
                return AssessmentResult(status="unsupported", reason="No holding detector")
        self.verification.reviewer = Reviewer()
        self.observe("obs")
        self.assess("obs", source="independent")
        record = self.verification.outcomes.find(None, "held")
        assessment = await self.verification.assess(record, self.session.results, monotonic() + 1)
        self.assertEqual(assessment.verdict, "unknown")
        self.assertEqual(record["run_status"], "unsupported")
        self.assertIsNotNone(self.verification.policy.check(["held"]))

    async def test_legacy_untimestamped_image_is_advice_not_strict_evidence(self):
        self.session.record_result(ToolCall(name="camera", id="legacy"), ToolResult(images={"front": "frame.png"}))
        self.assess("legacy:front")
        self.assertIsNotNone(self.verification.policy.check(["held"]))

    async def test_undeclared_goal_rejected_before_judgment_is_written(self):
        with self.assertRaises(ValueError):
            self.verification.validate([ActionAssessment(goal_id="invented", verdict="satisfied", reason="guess")])
        self.assertFalse(self.session.state.assessments)

    async def test_prediction_cannot_be_laundered_through_another_claim(self):
        self.observe("obs")
        state = self.session.state
        predicted = state.record_claims([StateClaim(subject="cup", property="holding.v1", value=True,
            basis="predicted", evidence=["obs"])], "predictor")[0]
        inferred = state.record_claims([StateClaim(subject="cup", property="holding.v1", value=True,
            evidence=[predicted])], "main_agent")[0]
        self.assess(inferred)
        self.assertIsNotNone(self.verification.policy.check(["held"]))

    async def test_bound_action_subject_must_match_goal(self):
        self.policy.subject_arguments["place"] = "object"
        self.observe("obs")
        self.assess("obs")
        self.assertIsNone(self.verification.policy.before_call(ToolCall(name="place", arguments={"object": "cup"}), None))
        self.assertIsNotNone(self.verification.policy.before_call(ToolCall(name="place", arguments={"object": "bowl"}), None))

    async def test_late_old_negative_cannot_revoke_new_observation(self):
        self.observe("new", 100.0)
        self.assess("new")
        self.observe("old", 98.0)
        self.assess("old", "unsatisfied")
        self.assertIsNone(self.verification.policy.check(["held"]))
