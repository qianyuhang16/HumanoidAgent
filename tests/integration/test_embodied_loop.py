"""The three responsibilities cooperate through a real Runtime and fake devices."""

from pathlib import Path
from tempfile import TemporaryDirectory
from time import time
import unittest

from primitive_vla.config.schema import AdapterSpec, AppConfig, AgentVerificationConfig
from primitive_vla.runtime.session import Runtime
from primitive_vla.state.contracts import Observation
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import FunctionTool, ToolDefinition, ToolResult
from primitive_vla.verification.contracts import VerificationPolicy, GoalSpec


class Camera:
    def __init__(self, options, context):
        async def observe(args, call):
            return ToolResult(observations=[Observation(id="fresh-frame", sensor="front", captured_at=time())])
        self.tools = [FunctionTool(ToolDefinition(name="camera", description="Read fresh evidence"), observe)]


class Planner:
    def __init__(self, options, context):
        self.turn = 0
    async def step(self, context, tools):
        self.turn += 1
        if self.turn == 1:
            return {"final": {"status": "completed", "reason": "Premature claim"}}
        if self.turn == 2:
            assert "requires current" in context.history[-1]["text"]
            return {"calls": [{"name": "camera"}]}
        return {"assessments": [{"goal_id": "held", "verdict": "satisfied", "reason": "Visible grip",
                                 "evidence": ["fresh-frame"]}],
                "claims": [{"subject": "cup", "property": "holding.v1", "value": True,
                            "evidence": ["fresh-frame"]}],
                "final": {"status": "completed", "reason": "Fresh evidence assessed", "evidence": ["fresh-frame"]}}


class EmbodiedLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_premature_completion_requires_evidence_then_same_turn_assessment_succeeds(self):
        config = AppConfig(model=AdapterSpec(adapter=__name__ + ":Planner"),
            tool_providers=[AdapterSpec(adapter=__name__ + ":Camera")],
            verification=AgentVerificationConfig(policy=VerificationPolicy(
                goals={"held": GoalSpec(description="Cup is held", subject="cup")}, completion=["held"])))
        with TemporaryDirectory() as tmp:
            runtime = Runtime(config, Path(tmp), EventLog())
            try:
                report = await runtime.run("Check the cup")
                self.assertEqual(report["status"], "completed")
                self.assertEqual(report["turns"], 3)
                self.assertEqual(report["steps"], 1)
                self.assertEqual(len(runtime.session.state.assessments), 1)
                self.assertEqual(runtime.session.state.query()[0]["source"], "main_agent")
            finally:
                await runtime.close()
