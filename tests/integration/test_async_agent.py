"""One model coordinates independent devices through the public Runtime/tools."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time
from types import SimpleNamespace
import unittest

from primitive_vla.agent.contracts import FinalAnswer, ModelReply
from primitive_vla.agent.memory import Session
from primitive_vla.config.schema import AdapterSpec, AppConfig, AgentLimits, AgentVerificationConfig
from primitive_vla.execution.contracts import ExecutionUpdate
from primitive_vla.runtime.session import Runtime
from primitive_vla.state.contracts import Observation
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import FunctionTool, ToolCall, ToolDefinition, ToolResult
from primitive_vla.verification.contracts import OutcomeAssessment


class Devices:
    def __init__(self, options, context):
        self.h = context.transport
        self.tools = [self.tool(name) for name in ("a", "b")]

    def tool(self, name):
        async def run(args, context):
            self.h.started[name].set()
            self.h.trace.append((name, "start"))
            await self.h.release[name].wait()
            self.h.trace.append((name, "end"))
            return ToolResult(observations=[Observation(id=name + "-obs", subject=name, captured_at=time(),
                data={"measured": True})], execution=ExecutionUpdate(phase="finished", control="released"))
        async def stop(call_id, reason):
            self.h.stops.append(name)
            return self.h.confirmed
        return FunctionTool(ToolDefinition(name=name + ".act", description=name + " action",
            device_id=name, resources=[name + ":body"], read_only=False, review_outcome=True), run, stop)


class Reviewer:
    def __init__(self, options, context):
        self.h = context.transport

    async def assess(self, context):
        self.h.reviews.append(context.call["name"])
        return OutcomeAssessment(verdict="satisfied", reason="Test observation received",
                                 evidence=[context.call["name"][0] + "-obs"])


class Model:
    def __init__(self, options, context):
        self.h, self.mode, self.step_number = context.transport, options.get("mode", "concurrent"), 0

    async def step(self, context, tools):
        self.h.contexts.append(context)
        step = self.step_number
        self.step_number += 1
        if self.mode == "finish":
            return ModelReply(final=FinalAnswer(status="blocked", reason="End test session"))
        if step == 0:
            return ModelReply(calls=[ToolCall(name="execution.submit", arguments={"tool": "a.act", "arguments": {}})])
        await self.h.started["a"].wait()
        if self.mode == "hang":
            await asyncio.Event().wait()
        if step == 1:
            return ModelReply(calls=[ToolCall(name="execution.submit", arguments={"tool": "b.act", "arguments": {}})])
        await self.h.started["b"].wait()
        if step == 2:
            return ModelReply(final=FinalAnswer(status="completed", reason="Premature test completion"))
        if step == 3:
            self.h.premature_feedback = context.history[-1]
            self.h.release["b"].set()
        elif step == 4:
            self.h.b_finished_while_a_active = any(r["device_id"] == "b" and r["status"] == "finished"
                                                  for r in context.executions) and not self.h.release["a"].is_set()
            self.h.release["a"].set()
        else:
            return ModelReply(final=FinalAnswer(status="completed", reason="Both results inspected"))
        return ModelReply(calls=[ToolCall(name="execution.wait", arguments={"after": context.execution_revision})])


class AsyncAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory()
        self.h = SimpleNamespace(started={n: asyncio.Event() for n in ("a", "b")},
            release={n: asyncio.Event() for n in ("a", "b")}, trace=[], stops=[], reviews=[], contexts=[],
            confirmed=True, premature_feedback=None, b_finished_while_a_active=False)
        self.runtimes = []

    async def asyncTearDown(self):
        for runtime in self.runtimes:
            await runtime.close()
        self.tmp.cleanup()

    def runtime(self, mode="concurrent", **limits):
        config = AppConfig(model=AdapterSpec(adapter=__name__ + ":Model", options={"mode": mode}),
            tool_providers=[AdapterSpec(adapter=__name__ + ":Devices")],
            verification=AgentVerificationConfig(mode="independent", reviewer=AdapterSpec(adapter=__name__ + ":Reviewer")),
            limits=AgentLimits(**limits))
        runtime = Runtime(config, Path(self.tmp.name), EventLog(), transport=self.h)
        self.runtimes.append(runtime)
        return runtime

    async def test_one_agent_submits_two_devices_observes_b_completion_and_continues_with_a(self):
        runtime = self.runtime()
        report = await asyncio.wait_for(runtime.run("A navigates while B manipulates"), 2)
        self.assertEqual(report["status"], "completed", report)
        self.assertTrue(self.h.b_finished_while_a_active)
        self.assertIn("remain queued or running", self.h.premature_feedback["text"])
        self.assertEqual(self.h.trace, [("a", "start"), ("b", "start"), ("b", "end"), ("a", "end")])
        self.assertEqual(self.h.reviews, ["b.act", "a.act"])
        self.assertEqual(len([r for r in report["results"] if r["tool"] in ("a.act", "b.act")]), 2)
        for job in runtime.queue.jobs.values():
            self.assertEqual(runtime.session.state.read(job.call.id)["kind"], "result")
        self.assertFalse(runtime.tools.leases)

    async def test_public_submit_rejects_invalid_targets_and_cancel_publishes_underlying_result(self):
        runtime = self.runtime()
        await runtime.start()
        for name, args in (("a.act", {"bad": 1}), ("state.query", {}), ("missing", {})):
            result = await runtime.tools.invoke(ToolCall(name="execution.submit", arguments={"tool": name, "arguments": args}))
            self.assertEqual(result.status, "rejected")
        call = ToolCall(name="execution.submit", arguments={"tool": "a.act", "arguments": {}})
        receipt = await runtime.tools.invoke(call)
        task_id = receipt.data["task"]["task_id"]
        again = await runtime.tools.invoke(call)
        self.assertEqual(again.data["task"]["task_id"], task_id)
        await asyncio.wait_for(self.h.started["a"].wait(), 1)
        queried = await runtime.tools.invoke(ToolCall(name="execution.query", arguments={"task_id": task_id}))
        self.assertEqual(queried.data["tasks"][0]["device_id"], "a")
        result = await runtime.tools.invoke(ToolCall(name="execution.cancel", arguments={"task_id": task_id}))
        self.assertEqual(result.data["task"]["status"], "cancelled")
        self.assertEqual(runtime.session.state.read(task_id)["payload"]["status"], "cancelled")
        self.assertEqual(self.h.stops, ["a"])

    async def test_end_session_cancels_waiting_work_and_stops_running_action(self):
        runtime = self.runtime(mode="finish")
        await runtime.start()
        runtime.queue.submit(ToolCall(name="a.act", id="running"))
        runtime.queue.submit(ToolCall(name="a.act", id="queued"))
        await asyncio.wait_for(self.h.started["a"].wait(), 1)
        report = await runtime.run("Stop this session")
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(self.h.trace, [("a", "start")])
        self.assertEqual(self.h.stops, ["a"])
        self.assertEqual({j.status for j in runtime.queue.jobs.values()}, {"cancelled"})

    async def test_timeout_and_external_cancellation_stop_async_actions(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                self.h.started["a"].clear()
                runtime = self.runtime(mode="hang", timeout_s=.2 if not cancel else 10)
                worker = asyncio.create_task(runtime.run("Long action"))
                await asyncio.wait_for(self.h.started["a"].wait(), 1)
                if cancel:
                    worker.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await worker
                else:
                    report = await worker
                    self.assertEqual(report["status"], "stopped")
                self.assertFalse(runtime.queue.pending)
                self.assertFalse(runtime.tools.leases)
        self.assertEqual(self.h.stops, ["a", "a"])

    async def test_unconfirmed_stop_changes_report_and_preserves_control_lock(self):
        self.h.confirmed = False
        runtime = self.runtime(mode="finish")
        await runtime.start()
        runtime.queue.submit(ToolCall(name="a.act", id="active"))
        await asyncio.wait_for(self.h.started["a"].wait(), 1)
        report = await runtime.run("Finish")
        self.assertTrue(report["control_locked"])
        self.assertEqual(runtime.queue.get("active").status, "unknown")

    async def test_snapshot_keeps_queue_arguments_but_restore_never_replays_jobs(self):
        runtime = self.runtime()
        await runtime.start()
        runtime.queue.submit(ToolCall(name="a.act", id="active"))
        runtime.queue.submit(ToolCall(name="a.act", id="queued"))
        snapshot = runtime.snapshot()
        self.assertEqual(snapshot["executions"][1]["arguments"], {})
        session = Session()
        session.restore(snapshot)
        self.assertTrue(all(row["historical"] and row["status"] == "interrupted" for row in session.executions))
