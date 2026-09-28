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
    async def run_agent(self, responder, max_turns=5, max_model_retries=2):
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
                report = await Agent(max_turns=max_turns, max_model_retries=max_model_retries).run("Move", AgentContext(model, runtime, session, log))
                return report, contexts, actions
            finally:
                await runtime.close()
                await model.close()

    async def test_format_repair_does_not_consume_the_remaining_task_turn(self):
        def respond(index, context):
            if index == 1:
                content = '{"calls":[{"name":"robot.move","arguments":{}}]}'
            elif index == 2:
                content = ''
            else:
                content = json.dumps({"final": {"status": "completed", "reason": "observed"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        report, contexts, actions = await self.run_agent(respond, max_turns=2)
        self.assertEqual(report.status, "completed", report.reason)
        self.assertEqual(report.turns, 2)
        self.assertEqual(len(contexts), 3)
        self.assertEqual(len(actions), 1)
        self.assertEqual((report.model_requests, report.model_errors), (3, 1))
        self.assertEqual(contexts[1]['remaining_turns'], contexts[2]['remaining_turns'])

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
        self.assertEqual((report.status, report.turns, report.steps), ("completed", 2, 1))
        self.assertEqual(len(actions), 1)
        self.assertEqual(contexts[2]["history"][-1]["kind"], "feedback")
        self.assertIn("no tools were executed", contexts[2]["history"][-1]["text"])

    async def test_repeated_invalid_output_has_its_own_limit_and_explicit_reason(self):
        report, contexts, actions = await self.run_agent(
            lambda *_: httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}), max_turns=3)
        self.assertEqual((report.status, report.turns, report.steps), ("stopped", 0, 0))
        self.assertIn("Model response recovery exhausted", report.reason)
        self.assertEqual((report.model_requests, report.model_errors), (3, 3))
        self.assertEqual(len(contexts), 3)
        self.assertFalse(actions)

    async def test_invalid_structure_uses_the_same_bounded_repair(self):
        report, contexts, actions = await self.run_agent(
            lambda *_: httpx.Response(200, json={"choices": [{"message": {"content": '{}'}}]}),
            max_model_retries=0)
        self.assertEqual(report.status, 'stopped')
        self.assertEqual((report.model_requests, report.model_errors), (1, 1))
        self.assertFalse(actions)

    async def test_retry_allowance_resets_after_a_valid_decision(self):
        def respond(index, context):
            if index in (1, 2, 4, 5):
                content = ''
            elif index == 3:
                content = '{"calls":[{"name":"robot.move","arguments":{}}]}'
            else:
                content = '{"final":{"status":"completed","reason":"observed"}}'
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        report, contexts, actions = await self.run_agent(respond, max_turns=2)
        self.assertEqual((report.status, report.turns, report.model_errors), ('completed', 2, 4))
        self.assertEqual(len(actions), 1)

    async def test_two_stage_task_continues_after_format_errors_without_replaying_stage_one(self):
        def respond(index, context):
            if index in (1, 4):
                content = '{"calls":[{"name":"robot.move","arguments":{}}]}'
            elif index in (2, 3):
                content = '{"calls":[]}\n{"calls":[],"final":null}'
            else:
                content = '{"final":{"status":"completed","reason":"Both stages verified"}}'
            return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})
        report, contexts, actions = await self.run_agent(respond, max_turns=3)
        self.assertEqual((report.status, report.turns, report.model_requests), ('completed', 3, 5))
        self.assertEqual(len(actions), 2)
        self.assertEqual(len(set(actions)), 2)
        self.assertEqual(sum(e['kind']=='tool_result' for e in contexts[3]['history']), 1)

    async def test_http_failure_is_not_misclassified_as_correctable_output(self):
        report, contexts, actions = await self.run_agent(lambda *_: httpx.Response(500, json={"error": "upstream"}))
        self.assertEqual(report.status, "failed")
        self.assertEqual(len(contexts), 1)
        self.assertFalse(actions)


if __name__ == "__main__":
    unittest.main()
