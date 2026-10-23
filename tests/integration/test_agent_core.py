"""Black-box tests of model-directed tool use; no symbolic task vocabulary."""

import asyncio
import unittest

from primitive_vla.agent.contracts import AgentContext, FinalAnswer, ModelReply
from primitive_vla.agent.core import Agent
from primitive_vla.agent.memory import Session
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import FunctionTool, ToolCall, ToolDefinition, ToolResult
from primitive_vla.tools.runtime import ToolRuntime


class ScriptedModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.contexts = []

    async def step(self, context, tools):
        self.contexts.append(context)
        reply = next(self.replies)
        return reply(context, tools) if callable(reply) else reply


def setup(replies, tools=(), **session_options):
    log, session = EventLog(), Session(**session_options)
    runtime = ToolRuntime([*session.tools(), *tools], log=log, stop_timeout_s=.02)
    model = ScriptedModel(replies)
    return AgentContext(model, runtime, session, log)


def call(name, **arguments):
    return ModelReply(calls=[ToolCall(name=name, arguments=arguments)])


def finish(status="completed"):
    return ModelReply(final=FinalAnswer(status=status, reason="Evidence reviewed"))


class AgentCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_arbitrary_tool_and_free_text_task_without_scene_or_goal_parser(self):
        received = []
        async def inspect(args, context):
            received.append(args)
            return ToolResult(data={"temperature": 32, "source": "sensor"})
        tool = FunctionTool(ToolDefinition(name="thermal.read", description="Read a sensor.",
            parameters={"type": "object", "properties": {"channel": {"type": "integer"}},
                        "required": ["channel"], "additionalProperties": False}), inspect)
        ctx = setup([call("thermal.read", channel=7), finish()], [tool])
        result = await Agent().run("查看第七路温度，报告是否适合实验。", ctx)
        self.assertEqual(result.status, "completed")
        self.assertEqual(received, [{"channel": 7}])
        self.assertEqual(ctx.model.contexts[1].history[-1]["data"]["temperature"], 32)

    async def test_invalid_arguments_are_feedback_and_do_not_execute(self):
        calls = []
        async def run(args, context):
            calls.append(args)
            return ToolResult(data={"ok": True})
        tool = FunctionTool(ToolDefinition(name="locate", description="Locate an object",
            parameters={"type": "object", "properties": {"query": {"type": "string"}},
                        "required": ["query"], "additionalProperties": False}), run)
        ctx = setup([call("locate", query=42), call("locate", query="cup"), finish()], [tool])
        result = await Agent().run("Locate", ctx)
        self.assertEqual(calls, [{"query": "cup"}])
        self.assertEqual(result.results[0]["status"], "rejected")
        self.assertEqual(result.status, "completed")

    async def test_failed_tool_can_be_followed_by_another_tool(self):
        async def failed(args, context):
            return ToolResult(status="error", text="Object occluded")
        async def other(args, context):
            return ToolResult(data={"visible": True})
        tools = [FunctionTool(ToolDefinition(name="camera.a", description="A"), failed),
                 FunctionTool(ToolDefinition(name="camera.b", description="B"), other)]
        ctx = setup([call("camera.a"), call("camera.b"), finish()], tools)
        result = await Agent().run("Inspect from a useful view", ctx)
        self.assertEqual([r["status"] for r in result.results], ["error", "ok"])
        self.assertEqual(result.status, "completed")

    async def test_finish_check_rejection_returns_to_model(self):
        class Check:
            async def check(self, answer, context):
                return "Need an observation" if not any(e["kind"] == "tool_result" for e in context.history) else None
        async def observe(args, context):
            return ToolResult(data={"measured": True})
        ctx = setup([finish(), call("observe"), finish()],
                    [FunctionTool(ToolDefinition(name="observe", description="Observe"), observe)])
        ctx.finish_check = Check()
        result = await Agent().run("Check", ctx)
        self.assertEqual(result.turns, 3)
        self.assertEqual(ctx.model.contexts[1].history[-1]["text"], "Need an observation")

    async def test_memory_and_retrieval_survive_context_truncation(self):
        async def observe(args, context):
            return ToolResult(data={"trial": "left battery unchanged"}, images={"camera": "frame.png"})
        ctx = setup([call("observe"),
                     ModelReply(calls=[ToolCall(name="observe")], memory="Left side already tried"),
                     call("session.read", index=2), finish()],
                    [FunctionTool(ToolDefinition(name="observe", description="Observe"), observe)],
                    history_limit=2)
        result = await Agent().run("Explore", ctx)
        self.assertIn("left battery", result.results[2]["data"]["content"])
        self.assertEqual(ctx.model.contexts[2].working_memory, "Left side already tried")
        self.assertEqual(result.results[2]["images"], {"camera": "frame.png"})

    async def test_final_evidence_cannot_reference_invented_call(self):
        wrong = ModelReply(final=FinalAnswer(status="completed", reason="Done", evidence=["imaginary"]))
        ctx = setup([wrong, finish("blocked")])
        report = await Agent().run("Task", ctx)
        self.assertEqual(report.status, "blocked")
        self.assertIn("recorded calls, observations or judgments", ctx.model.contexts[1].history[-1]["text"])

    async def test_calls_budget_stops_dispatch_but_allows_final_explanation(self):
        calls = []
        async def run(args, context):
            calls.append(1)
            return ToolResult()
        ctx = setup([ModelReply(calls=[ToolCall(name="read"), ToolCall(name="read")]), finish("blocked")],
                    [FunctionTool(ToolDefinition(name="read", description="Read"), run)])
        report = await Agent(max_calls=1).run("Task", ctx)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report.results[1]["status"], "rejected")
        self.assertEqual(report.status, "blocked")
