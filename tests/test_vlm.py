import asyncio
import base64
from dataclasses import asdict
from importlib.util import find_spec
import json
from tempfile import TemporaryDirectory
import unittest

HTTP_AVAILABLE = find_spec("httpx") is not None
if HTTP_AVAILABLE:
    import httpx
    from primitive_vla.vlm.deepseek import DeepSeekVLM

from primitive_vla.memory import Memory
from primitive_vla.planning import ModelPlanner, PlanningContext
from primitive_vla.planning.actions import ACTION_SCHEMA
from primitive_vla.planning.context import INSTRUCTIONS
from primitive_vla.runtime.agent import Agent
from primitive_vla.runtime.session import Limits
from primitive_vla.skills import SkillStore
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.registry import ToolRegistry
from primitive_vla.vlm import FunctionCall, VLMRequest, VLMResponse
from tests.support import Provider, spec


def model_response(action="finish", arguments=None, **choice_changes):
    return httpx.Response(200, json={"choices": [{
        "finish_reason": "tool_calls", "message": {"tool_calls": [{
            "type": "function", "function": {"name": action, "arguments": json.dumps(
                arguments if arguments is not None else {"message": "done", "outcome": "success"})},
        }]}, **choice_changes,
    }]})


@unittest.skipUnless(HTTP_AVAILABLE, "install primitive-vla[deepseek] to test the model adapter")
class DeepSeekTests(unittest.IsolatedAsyncioTestCase):
    def context(self, result=None, schema=ACTION_SCHEMA):
        return PlanningContext({"instructions": INSTRUCTIONS, "last_result": result or {},
                                "session": {"goal": "检查环境"}}, schema)

    async def test_transport_accepts_non_planning_prompts_and_plain_text(self):
        async def respond(request):
            body = json.loads(request.content)
            self.assertEqual(body["messages"][0]["content"], "Translate this paragraph")
            self.assertNotIn("tools", body)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "翻译"}}]})

        vlm = DeepSeekVLM("test-key", transport=httpx.MockTransport(respond))
        result = await vlm.generate(VLMRequest("Translate this paragraph", "Text"))
        self.assertEqual(result.text, "翻译")
        self.assertEqual(result.calls, ())

    async def test_swapping_vlm_keeps_the_same_planning_prompt(self):
        class Model:
            async def generate(self, request):
                self.request = request
                return VLMResponse((FunctionCall("wait", {"seconds": 1}),))

        first, second = Model(), Model()
        context = self.context()
        self.assertEqual(await ModelPlanner(first).decide(context), await ModelPlanner(second).decide(context))
        self.assertEqual(first.request, second.request)
        self.assertEqual(first.request.instructions, INSTRUCTIONS)

    async def test_function_schema_is_taken_from_context(self):
        schema = {"oneOf": [{"properties": {
            "action": {"const": "new_framework_action"},
            "arguments": {"type": "object", "properties": {"query": {"type": "string"}}},
        }}]}
        seen = []

        async def respond(request):
            seen.append(json.loads(request.content))
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            return model_response("new_framework_action", {"query": "杯子"})

        planner = ModelPlanner(DeepSeekVLM("test-key", transport=httpx.MockTransport(respond)))
        decision = await planner.decide(self.context(schema=schema))
        self.assertEqual(decision.action, "new_framework_action")
        self.assertEqual(decision.arguments, {"query": "杯子"})
        self.assertEqual(seen[0]["model"], "deepseek-flash")
        self.assertEqual(seen[0]["tools"][0]["function"]["parameters"],
                         schema["oneOf"][0]["properties"]["arguments"])
        self.assertEqual(seen[0]["thinking"], {"type": "disabled"})
        self.assertNotIn("test-key", json.dumps(seen))

    async def test_requested_image_contains_bytes_and_history_is_not_bulk_loaded(self):
        with TemporaryDirectory() as directory:
            memory = Memory(directory)
            requested = memory.put_resource(b"requested-pixels", "image/png")
            other = memory.put_resource(b"private-unrequested-pixels", "image/png")
            seen = []

            async def respond(request):
                seen.append(json.loads(request.content))
                return model_response()

            planner = ModelPlanner(DeepSeekVLM("test-key", transport=httpx.MockTransport(respond)),
                                   read_resource=memory.read_resource)
            await planner.decide(self.context({"resource_refs": [asdict(other)]}))
            self.assertEqual(len(seen[-1]["messages"][1]["content"]), 1)
            await planner.decide(self.context(asdict(requested)))
            content = seen[-1]["messages"][1]["content"]
            self.assertEqual(base64.b64decode(content[1]["image_url"]["url"].split(",")[1]),
                             b"requested-pixels")
            self.assertNotIn(base64.b64encode(b"private-unrequested-pixels").decode(), json.dumps(seen))
            memory.close()

    async def test_http_failure_and_tool_result_flow_through_existing_runtime(self):
        requests = []

        async def respond(request):
            body = json.loads(request.content)
            data = json.loads(body["messages"][1]["content"][0]["text"])
            requests.append(data)
            if len(requests) == 1:
                return httpx.Response(429, json={"error": {"message": "limited"}})
            if len(requests) == 2:
                self.assertEqual(data["last_result"]["kind"], "planner_error")
                return model_response("call_tool", {"name": "state.new", "arguments": {"value": 7}})
            self.assertEqual(data["last_result"]["status"], "completed")
            return model_response()

        with TemporaryDirectory() as directory:
            memory, registry, resources = Memory(directory), ToolRegistry(), ResourceManager()
            provider = Provider()
            registry.register(spec("state.new"), provider)
            resources.define_robot("r", set())
            planner = ModelPlanner(DeepSeekVLM("test-secret", transport=httpx.MockTransport(respond)))
            agent = Agent(planner, registry, memory, SkillStore(), resources, Limits(max_decisions=5))
            session = agent.create_session("Inspect state", "r", "test")
            await agent.run(session.id)
            self.assertEqual(session.status, "completed")
            self.assertEqual(session.decisions, 3)
            self.assertEqual(provider.executions, 1)
            self.assertNotIn("test-secret", json.dumps([r.to_dict() for r in memory.search()]))
            await agent.close()

    async def test_incomplete_or_multiple_decisions_are_not_executed(self):
        cases = [
            model_response(finish_reason="length"),
            model_response(message={"tool_calls": [{"function": {"name": "wait", "arguments": "{}"}}] * 2}),
            model_response(message={"tool_calls": [{"function": {"name": "finish", "arguments": "{"}}]}),
        ]
        for reply in cases:
            with self.subTest(reply=reply.json()):
                planner = ModelPlanner(DeepSeekVLM("test-key", transport=httpx.MockTransport(lambda _: reply)))
                with self.assertRaises(ValueError):
                    await planner.decide(self.context())

    async def test_action_contract_errors_are_validated_by_runtime(self):
        async def respond(request):
            data = json.loads(json.loads(request.content)["messages"][1]["content"][0]["text"])
            if not data["last_result"]:
                return model_response("call_tool", {"arguments": {}})
            self.assertEqual(data["last_result"]["kind"], "interface_error")
            return model_response("finish", {"message": "Invalid call corrected", "outcome": "failed"})

        with TemporaryDirectory() as directory:
            agent = Agent(ModelPlanner(DeepSeekVLM("test-key", transport=httpx.MockTransport(respond))),
                          ToolRegistry(), Memory(directory), SkillStore(), ResourceManager(),
                          Limits(context_chars=64000))
            session = agent.create_session("Check contract", "r", "test")
            await agent.run(session.id)
            self.assertEqual(session.status, "failed")
            self.assertEqual(session.tool_calls, 0)
            await agent.close()

    async def test_pause_cancels_inflight_http_request(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def respond(request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with TemporaryDirectory() as directory:
            agent = Agent(ModelPlanner(DeepSeekVLM("test-key", transport=httpx.MockTransport(respond))),
                          ToolRegistry(), Memory(directory), SkillStore(), ResourceManager())
            session = agent.create_session("Wait for model", "r", "test")
            task = asyncio.create_task(agent.run(session.id))
            await asyncio.wait_for(started.wait(), 1)
            agent.pause(session.id)
            await asyncio.wait_for(task, 1)
            self.assertTrue(cancelled.is_set())
            self.assertEqual(session.status, "paused")
            self.assertEqual(session.decisions, 1)
            await agent.close()
