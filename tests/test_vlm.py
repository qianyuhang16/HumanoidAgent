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
from primitive_vla.tools.contracts import ToolResult
from primitive_vla.vlm import FunctionCall, VLMRequest, VLMResponse
from tests.support import Provider, spec


def model_response(action="finish", arguments=None, **choice_changes):
    return httpx.Response(200, json={"choices": [{
        "finish_reason": "tool_calls", "message": {"tool_calls": [{
            "type": "function", "function": {"name": "decide", "arguments": json.dumps({
                "action": action, "arguments": arguments if arguments is not None else
                {"message": "done", "outcome": "success"}})},
        }]}, **choice_changes,
    }]})


def responses_reply(action="finish", arguments=None):
    return httpx.Response(200, json={"status": "completed", "output": [{
        "type": "function_call", "name": "decide", "arguments": json.dumps({
            "action": action, "arguments": arguments if arguments is not None else
            {"message": "done", "outcome": "success"},
        }),
    }]})


@unittest.skipUnless(HTTP_AVAILABLE, "install primitive-vla[deepseek] to test the model adapter")
class DeepSeekTests(unittest.IsolatedAsyncioTestCase):
    def context(self, result=None, schema=ACTION_SCHEMA, images=()):
        return PlanningContext({"instructions": INSTRUCTIONS, "last_result": result or {},
                                "images": [{"description": ref["description"]} for ref in images],
                                "goal": "检查环境"}, schema, tuple(images))

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
                return VLMResponse((FunctionCall("decide", {"action": "wait", "arguments": {"seconds": 1}}),))

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
        self.assertEqual(len(seen[0]["tools"]), 1)
        self.assertEqual(seen[0]["tools"][0]["function"]["name"], "decide")
        self.assertEqual(seen[0]["tool_choice"], {"type": "function", "function": {"name": "decide"}})
        self.assertEqual(seen[0]["tools"][0]["function"]["parameters"], {"type": "object", **schema})
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
            self.assertEqual(json.loads(seen[-1]["messages"][1]["content"][0]["text"])["images"], [])
            await planner.decide(self.context(asdict(requested), images=(asdict(requested),)))
            content = seen[-1]["messages"][1]["content"]
            self.assertEqual(base64.b64decode(content[1]["image_url"]["url"].split(",")[1]),
                             b"requested-pixels")
            attached = json.loads(content[0]["text"])["images"]
            self.assertEqual(attached, [{"description": requested.description}])
            self.assertNotIn(base64.b64encode(b"private-unrequested-pixels").decode(), json.dumps(seen))
            memory.close()

    async def test_http_failure_and_tool_result_flow_through_existing_runtime(self):
        requests = []

        async def respond(request):
            self.assertEqual(request.url.path, "/responses")
            body = json.loads(request.content)
            self.assertEqual(body["reasoning"], {"effort": "none"})
            items = body["input"]
            requests.append(items)
            if len(requests) == 1:
                return httpx.Response(429, json={"error": {"message": "limited"}})
            if len(requests) == 2:
                self.assertIn("planner_error", items[-1]["content"][0]["text"])
                return responses_reply("call_tool", {"name": "state.new", "arguments": {"value": 7}})
            call, result = items[-2:]
            self.assertEqual(call["type"], "function_call")
            self.assertEqual(result["type"], "function_call_output")
            self.assertEqual(result["call_id"], call["call_id"])
            self.assertEqual(json.loads(call["arguments"])["arguments"]["name"], "state.new")
            self.assertEqual(json.loads(result["output"][0]["text"])["status"], "completed")
            image = result["output"][1]
            self.assertEqual(image["type"], "input_image")
            self.assertEqual(base64.b64decode(image["image_url"].split(",")[1]), b"visible camera frame")
            self.assertFalse(any(part["type"] == "input_image" for item in items for part in item.get("content", [])))
            return responses_reply()

        with TemporaryDirectory() as directory:
            memory, registry, resources = Memory(directory), ToolRegistry(), ResourceManager()
            image = memory.put_resource(b"visible camera frame", "image/png")
            provider = Provider(ToolResult({"value": 1}, (image,)))
            registry.register(spec("state.new"), provider)
            resources.define_robot("r", set())
            planner = ModelPlanner(DeepSeekVLM("test-secret", transport=httpx.MockTransport(respond)),
                                   read_resource=memory.read_resource)
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
            items = json.loads(request.content)["input"]
            if len(items) == 1:
                return responses_reply("call_tool", {"arguments": {}})
            self.assertIn("interface_error", items[-1]["content"][0]["text"])
            return responses_reply("finish", {"message": "Invalid call corrected", "outcome": "failed"})

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
