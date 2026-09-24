"""Task evidence and planning regressions; no GPU or credentials needed."""

from copy import deepcopy
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from primitive_vla.agent.contracts import AgentContext, FinalAnswer, ModelReply
from primitive_vla.agent.core import Agent
from primitive_vla.agent.memory import Session
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import PluginContext, ToolCall, FunctionTool, ToolDefinition, ToolResult
from primitive_vla.tools.runtime import ToolRuntime
from primitive_vla_tabletop.evaluation import evaluate_samples, task_goal, TabletopFinishCheck
from primitive_vla_tabletop.provider import TabletopTools


def state(**changes):
    return {"bilateral_contacts": False, "lift_m": 0., "inside_zones": ["left"],
            "table_contact": True, "finger_contact": False, "linear_speed_m_s": 0.,
            "angular_speed_rad_s": 0., **changes}


class EvaluationTests(unittest.TestCase):
    def test_sorting_requires_both_objects_and_correct_zones(self):
        goal = task_goal({"scenario": "sorting"})
        sample = {"target": state(), "blue": state(inside_zones=[])}
        result = evaluate_samples(goal, [sample] * 120)
        self.assertFalse(result["success"])
        self.assertEqual([c["satisfied"] for c in result["criteria"]], [True, False])
        sample["blue"]["inside_zones"] = ["right"]
        self.assertTrue(evaluate_samples(goal, [sample] * 120)["success"])
        sample["target"]["inside_zones"], sample["blue"]["inside_zones"] = ["right"], ["left"]
        self.assertFalse(evaluate_samples(goal, [sample] * 120)["success"])

    def test_hovering_unreleased_unsettled_or_absent_is_not_placed(self):
        goal = [{"target": "target", "predicate": "placed", "zone": "left"}]
        for invalid in ({"table_contact": False}, {"finger_contact": True},
                        {"linear_speed_m_s": .1}, {"angular_speed_rad_s": 1.}, {"inside_zones": []}):
            with self.subTest(invalid=invalid):
                self.assertFalse(evaluate_samples(goal, [{"target": state(**invalid)}] * 120)["success"])
        self.assertFalse(evaluate_samples(goal, [{}] * 120)["success"])

    def test_grasp_requires_correct_object_contact_and_lift_through_end(self):
        goal = task_goal({})
        good = {"target": state(bilateral_contacts=True, lift_m=.15)}
        bad = {"target": state(bilateral_contacts=True, lift_m=0.)}
        self.assertTrue(evaluate_samples(goal, [good] * 120)["success"])
        self.assertFalse(evaluate_samples(goal, [good] * 119 + [bad])["success"])
        self.assertFalse(evaluate_samples(goal, [good] * 60 + [bad] + [good] * 59)["success"])
        self.assertFalse(evaluate_samples(goal, [{"blue": good["target"]}] * 120)["success"])
        self.assertFalse(evaluate_samples(goal, [{"target": state(lift_m=.15)}] * 120)["success"])

    def test_goal_validation_cannot_accept_vacuous_or_malformed_contracts(self):
        for goal in ([], [{"target": "target", "predicate": "placed"}],
                     [{"target": "target", "predicate": "held", "zone": "left"}]):
            with self.subTest(goal=goal), self.assertRaises(ValueError):
                task_goal({"goal": goal})


class FinishTests(unittest.IsolatedAsyncioTestCase):
    async def test_finish_gate_rejects_stale_failed_or_uncited_verification(self):
        log = EventLog()
        check = TabletopFinishCheck({}, PluginContext(Path("."), Path("."), log))
        answer = FinalAnswer(status="completed", reason="done", evidence=["v"])
        self.assertIsNotNone(await check.check(answer, None))
        log.write("tool_finished", call={"id": "v", "name": "tabletop.verify"},
                  result={"status": "ok", "data": {"verdict": "satisfied"}})
        self.assertIsNone(await check.check(answer, None))
        self.assertIsNotNone(await check.check(answer.model_copy(update={"evidence": []}), None))
        log.write("tool_started", call={"id": "motion", "name": "robot.gripper"})
        self.assertIsNotNone(await check.check(answer, None))
        log.write("tool_finished", call={"id": "v2", "name": "tabletop.verify"},
                  result={"status": "ok", "data": {"verdict": "unsatisfied"}})
        self.assertIsNotNone(await check.check(answer.model_copy(update={"evidence": ["v2"]}), None))
        self.assertIsNone(await check.check(FinalAnswer(status="blocked", reason="obstacle"), None))

    async def test_actual_agent_loop_corrects_premature_completion(self):
        log, session = EventLog(), Session()
        async def verify(args, context):
            return ToolResult(data={"verdict": "satisfied"})
        runtime = ToolRuntime([FunctionTool(ToolDefinition(name="tabletop.verify", description="test verifier"), verify)], log=log)
        check = TabletopFinishCheck({}, PluginContext(Path("."), Path("."), log))
        class FixtureModel:
            async def step(self, context, tools):
                results = [e for e in context.history if e["kind"] == "tool_result"]
                if results:
                    return ModelReply(final=FinalAnswer(status="completed", reason="verified", evidence=[results[-1]["call_id"]]))
                if any(e["kind"] == "feedback" for e in context.history):
                    return ModelReply(calls=[ToolCall(name="tabletop.verify")])
                return ModelReply(final=FinalAnswer(status="completed", reason="unsupported claim"))
        result = await Agent(max_turns=4).run("verify", AgentContext(FixtureModel(), runtime, session, log, check))
        self.assertEqual((result.status, result.turns, result.steps), ("completed", 3, 1))
        self.assertTrue(any(e["kind"] == "feedback" for e in session.events))
        await runtime.close()

    async def test_plan_requires_real_evidence_and_acyclic_dependencies(self):
        with TemporaryDirectory() as temp:
            log = EventLog()
            provider = TabletopTools({"python_executable": "python", "robot_urdf": "unused", "models_dir": "unused"},
                                    PluginContext(Path(temp), Path(temp), log))
            runtime = ToolRuntime(provider.tools, log=log)
            step = {"id": "a", "description": "inspect", "status": "pending", "depends_on": [], "evidence_call_ids": []}
            async def plan(steps):
                return await runtime.invoke(ToolCall(name="task.plan", arguments={"rationale": "test", "steps": steps}))
            self.assertEqual((await plan([step])).status, "ok")
            invalid = deepcopy(step)
            invalid["evidence_call_ids"] = ["invented"]
            self.assertEqual((await plan([invalid])).status, "rejected")
            invalid["evidence_call_ids"], invalid["depends_on"] = [], ["a"]
            self.assertEqual((await plan([invalid])).status, "rejected")
            self.assertEqual((await plan([step, step])).status, "rejected")
            await runtime.close()
            await provider.close()


@unittest.skipUnless(importlib.util.find_spec("numpy"), "Optional geometry dependency: numpy")
class ClearanceTests(unittest.TestCase):
    def test_clear_obstructed_and_missing_depth(self):
        import numpy as np
        from primitive_vla_tabletop.geometry import clearance
        transform = np.diag([1., -1., -1., 1.])
        transform[:3, 3] = [.45, 0, .8]
        frame = {"sensor_depth": np.full((100, 100), .8),
                 "intrinsics": np.array([[300, 0, 50], [0, 300, 50], [0, 0, 1.]]),
                 "world_from_camera": transform}
        self.assertEqual(clearance(frame, [.45, 0, .03])["verdict"], "feasible")
        frame["sensor_depth"][45:55, 45:55] = .6
        self.assertEqual(clearance(frame, [.45, 0, .03])["verdict"], "infeasible")
        frame["sensor_depth"][:] = np.nan
        self.assertEqual(clearance(frame, [.45, 0, .03])["verdict"], "unknown")
        frame["sensor_depth"][:20, :20] = .8
        self.assertEqual(clearance(frame, [.45, 0, .03])["verdict"], "unknown")


if __name__ == "__main__":
    unittest.main()
