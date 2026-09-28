"""Malformed model wire output must not abort or replay a physical action."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx

from primitive_vla.agent.contracts import AgentContext
from primitive_vla.agent.core import Agent
from primitive_vla.agent.memory import Session
from primitive_vla.agent.model import JSONModel
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import FunctionTool, PluginContext, ToolDefinition, ToolResult
from primitive_vla.tools.runtime import ToolRuntime


class ModelRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def run_agent(self, responder, max_turns=5):
        contexts, actions = [], []
        def transport(request):
            body = json.loads(request.content)
            context = json.loads(body["messages"][1]["content"][0]["text"])
            contexts.append(context)
            return responder(len(contexts), context)

        async def move(args, context):
            actions.append(context.call_id)
            return ToolResult(data={"moved": True})

        async def stop(call_id, reason):
            return True

        with TemporaryDirectory() as temp:
            log, session = EventLog(), Session()
            model = JSONModel({"model": "fixture", "base_url": "https://model.invalid"},
                              PluginContext(Path(temp), Path(temp), log, transport=httpx.MockTransport(transport)))
            runtime = ToolRuntime([FunctionTool(ToolDefinition(name="robot.move", description="Move once",
                                      read_only=False, resources=["robot"]), move, stop)], log=log)
            try:
                report = await Agent(max_turns=max_turns).run("Move", AgentContext(model, runtime, session, log))
                return report, contexts, actions
            finally:
                await runtime.close()
                await model.close()

    async def test_truncated_response_is_feedback_and_never_replays_action(self):
        def respond(index, context):
            if index == 1:
                content = '{"calls":[{"name":"robot.move","arguments":{}}]}'
            elif index == 2:
                content = '{"calls":[{"name":"robot.move","arguments":{}}'
            else:
                content = json.dumps({"final": {"status": "completed", "reason": "observed",
                                     "evidence": [next(e['call_id'] for e in context['history'] if e['kind']=='tool_result')]}})
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        report, contexts, actions = await self.run_agent(respond)
        self.assertEqual((report.status, report.turns, report.steps), ("completed", 3, 1))
        self.assertEqual(len(actions), 1)
        self.assertEqual(contexts[2]["history"][-1]["kind"], "feedback")
        self.assertIn("no tools were executed", contexts[2]["history"][-1]["text"])

    async def test_repeated_invalid_output_is_bounded_by_turn_budget(self):
        report, contexts, actions = await self.run_agent(
            lambda *_: httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}), max_turns=3)
        self.assertEqual((report.status, report.turns, report.steps), ("stopped", 3, 0))
        self.assertEqual(len(contexts), 3)
        self.assertFalse(actions)

    async def test_http_failure_is_not_misclassified_as_correctable_output(self):
        report, contexts, actions = await self.run_agent(lambda *_: httpx.Response(500, json={"error": "upstream"}))
        self.assertEqual(report.status, "failed")
        self.assertEqual(len(contexts), 1)
        self.assertFalse(actions)


if __name__ == "__main__":
    unittest.main()
