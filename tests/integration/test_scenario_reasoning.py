"""Feedback-dependent adapter choices, not a test of an actual model's intelligence."""

import unittest

from primitive_vla.agent.contracts import AgentContext, FinalAnswer, ModelReply
from primitive_vla.agent.core import Agent
from primitive_vla.agent.memory import Session
from primitive_vla.execution.runtime import ExecutionManager
from primitive_vla.state.contracts import Observation
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import FunctionTool, ToolCall, ToolDefinition, ToolResult


class ScenarioReasoningTests(unittest.IsolatedAsyncioTestCase):
    async def test_observation_changes_model_selected_sequence_without_state_claims(self):
        for initially_lit in (True, False):
            with self.subTest(initially_lit=initially_lit):
                session, log = Session(), EventLog()
                actions = []
                frames = []

                async def place(args, context):
                    actions.append("place")
                    return ToolResult(text="Physical insertion completed")

                async def reverse(args, context):
                    actions.append("reverse-left")
                    return ToolResult(text="Physical adjustment completed")

                async def observe(args, context):
                    ref = f"frame-{len(frames)}"
                    frames.append(ref)
                    return ToolResult(observations=[Observation(id=ref, sensor="camera",
                        captured_at=session.state.clock(), data={"indicator_lit": initially_lit or len(actions) > 1})])

                async def stop(*args):
                    return True

                class FeedbackModel:
                    async def step(self, context, tools):
                        # The fixture supplies decisions at the same seam as a
                        # real model. The Agent cannot see or execute its logic.
                        assert context.state == ()
                        results = [r for r in context.history if r["kind"] == "tool_result"]
                        if not results:
                            return ModelReply(calls=[ToolCall(name="battery.place")])
                        last = results[-1]
                        if last["tool"] != "remote.observe":
                            return ModelReply(calls=[ToolCall(name="remote.observe")])
                        observation = last["observations"][0]
                        if observation["data"]["indicator_lit"]:
                            return ModelReply(final=FinalAnswer(status="completed", reason="Indicator is lit",
                                                                evidence=[observation["id"]]))
                        return ModelReply(calls=[ToolCall(name="battery.reverse_left")])

                tools = [FunctionTool(ToolDefinition(name=name, description=name,
                    read_only=False, resources=["robot.arms"]), fn, stop)
                    for name, fn in (("battery.place", place), ("battery.reverse_left", reverse))]
                tools.append(FunctionTool(ToolDefinition(name="remote.observe", description="Observe indicator"), observe))
                runtime = ExecutionManager(tools, state=session.state, log=log)
                try:
                    report = await Agent().run("Install the battery and check operation",
                        AgentContext(FeedbackModel(), runtime, session, log))
                    self.assertEqual(report.status, "completed", report.reason)
                    self.assertEqual(actions, ["place"] if initially_lit else ["place", "reverse-left"])
                    self.assertEqual(len(frames), 1 if initially_lit else 2)
                    self.assertFalse(any(r["kind"] == "claim" for r in session.state.records.values()))
                    self.assertEqual(session.state.assessments, [])
                finally:
                    await runtime.close()
