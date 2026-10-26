"""Queue/control contracts exercised without robot drivers or model services."""

import asyncio
import unittest

from primitive_vla.execution.contracts import ExecutionUpdate
from primitive_vla.execution.queue import ActionQueue
from primitive_vla.execution.runtime import ExecutionManager
from primitive_vla.tools import FunctionTool, ToolCall, ToolDefinition, ToolResult


class ActionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entered, self.release, self.stops = {}, {}, []
        self.published = []
        self.runtime = ExecutionManager(stop_timeout_s=.02)
        self.queue = ActionQueue(self.runtime, publish=lambda call, result: self.published.append((call, result)))

    async def asyncTearDown(self):
        await self.runtime.close()

    def tool(self, name, device, resources=None, confirmed=True):
        async def run(args, context):
            self.entered.setdefault(context.call_id, asyncio.Event()).set()
            await self.release.setdefault(context.call_id, asyncio.Event()).wait()
            return ToolResult(execution=ExecutionUpdate(phase="finished", control="released"))
        async def stop(call_id, reason):
            self.stops.append(call_id)
            return confirmed
        self.runtime.register(FunctionTool(ToolDefinition(name=name, description=name, device_id=device,
            read_only=False, resources=resources or [name]), run, stop))

    async def started(self, *ids):
        await asyncio.wait_for(asyncio.gather(*(
            self.entered.setdefault(i, asyncio.Event()).wait() for i in ids)), .5)

    async def done(self, task_id):
        job = self.queue.get(task_id)
        await asyncio.wait_for(asyncio.shield(job.runner), .5)
        await asyncio.sleep(0)
        self.assertIsNotNone(job.result)

    async def test_same_device_fifo_different_devices_parallel_and_direct_calls_cannot_overtake(self):
        self.tool("a.move", "a")
        self.tool("a.grip", "a")  # Different resources still share the device queue.
        self.tool("b.move", "b")
        for name, task_id in (("a.move", "a1"), ("a.grip", "a2"), ("b.move", "b1")):
            self.queue.submit(ToolCall(name=name, id=task_id))
        await self.started("a1", "b1")
        self.assertEqual(self.queue.get("a2").status, "queued")
        rejected = await self.runtime.invoke(ToolCall(name="a.grip"))
        self.assertEqual(rejected.status, "rejected")
        self.release["a1"].set()
        await self.done("a1")
        await self.started("a2")
        self.assertIsNone(self.queue.get("b1").result)
        self.release["a2"].set()
        self.release["b1"].set()
        await self.done("a2")
        await self.done("b1")
        self.assertEqual(len(self.published), 3)

    async def test_shared_resources_serialize_without_blocking_independent_device(self):
        self.tool("a", "a", ["station"])
        self.tool("b", "b", ["station"])
        self.tool("c", "c")
        for name in ("a", "b", "c"):
            self.queue.submit(ToolCall(name=name, id=name))
        await self.started("a", "c")
        self.assertEqual(self.queue.get("b").status, "queued")
        self.release["a"].set()
        await self.done("a")
        await self.started("b")

    async def test_queued_cancel_never_dispatches_running_cancel_waits_for_stop(self):
        self.tool("move", "a")
        self.queue.submit(ToolCall(name="move", id="active"))
        self.queue.submit(ToolCall(name="move", id="waiting"))
        await self.started("active")
        self.assertEqual((await self.queue.cancel("waiting"))["status"], "cancelled")
        self.assertNotIn("waiting", self.entered)
        self.assertEqual((await self.queue.cancel("active"))["control"], "released")
        self.assertEqual(self.stops, ["active"])
        self.assertFalse(self.runtime.leases)
        self.assertEqual(len(self.published), 2)

    async def test_unconfirmed_stop_is_unknown_and_shutdown_never_dispatches_tail(self):
        self.tool("move", "a", confirmed=False)
        self.queue.submit(ToolCall(name="move", id="active"))
        self.queue.submit(ToolCall(name="move", id="tail"))
        await self.started("active")
        await self.queue.close()
        self.assertEqual(self.queue.get("active").status, "unknown")
        self.assertEqual(self.queue.get("tail").status, "cancelled")
        self.assertTrue(self.runtime.control_locked)
        self.assertNotIn("tail", self.entered)

    async def test_submission_validates_deduplicates_and_does_not_dispatch_invalid_calls(self):
        self.tool("move", "a")
        for call in (ToolCall(name="missing"), ToolCall(name="move", arguments={"invented": True})):
            with self.assertRaises(ValueError):
                self.queue.submit(call)
        self.assertFalse(self.queue.jobs)
        call = ToolCall(name="move", id="same")
        first = self.queue.submit(call)
        self.assertEqual(self.queue.submit(call)["task_id"], first["task_id"])
        with self.assertRaises(ValueError):
            self.queue.submit(call.model_copy(update={"arguments": {"other": 1}}))
        await self.started("same")
        self.assertEqual(len(self.queue.jobs), 1)

    async def test_live_preconditions_are_checked_at_dispatch_not_only_admission(self):
        self.tool("move", "a")
        self.queue.submit(ToolCall(name="move", id="first"))
        self.queue.submit(ToolCall(name="move", id="second"))
        await self.started("first")
        self.runtime.precondition = lambda *args: "Evidence no longer valid"
        self.release["first"].set()
        await self.done("first")
        await self.done("second")
        self.assertEqual(self.queue.get("second").status, "rejected")
        self.assertNotIn("second", self.entered)

    async def test_revision_wait_returns_on_change_and_can_time_out_without_cancelling_action(self):
        self.tool("move", "a")
        self.queue.submit(ToolCall(name="move", id="task"))
        await self.started("task")
        revision = self.queue.revision
        unchanged = await self.queue.wait(revision, .001)
        self.assertEqual(unchanged["revision"], revision)
        waiter = asyncio.create_task(self.queue.wait(revision, .5))
        self.release["task"].set()
        changed = await waiter
        self.assertGreater(changed["revision"], revision)
        self.assertEqual(changed["tasks"][0]["status"], "finished")
