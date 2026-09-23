"""Tool dispatch contracts: validation, resources, deduplication and safe termination."""

import asyncio
import unittest
from time import monotonic

from primitive_vla.tools import FunctionTool, ToolCall, ToolDefinition, ToolResult
from primitive_vla.tools.runtime import ToolRuntime


class ToolContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_identical_call_is_executed_once(self):
        entered = []
        async def run(args, context):
            entered.append(args)
            await asyncio.sleep(.01)
            return ToolResult(data={"value": 1})
        tool = FunctionTool(ToolDefinition(name="test.read", description="Read"), run)
        runtime = ToolRuntime([tool])
        request = ToolCall(name="test.read")
        first, second = await asyncio.gather(runtime.invoke(request), runtime.invoke(request))
        first.data["value"] = 9
        self.assertEqual(second.data["value"], 1)
        self.assertEqual(len(entered), 1)
        rejected = await runtime.invoke(request.model_copy(update={"arguments": {"x": 1}}))
        self.assertEqual(rejected.status, "rejected")

    async def test_unknown_and_denied_tools_do_not_dispatch(self):
        async def never(args, context):
            self.fail("Dispatched a denied tool")
        runtime = ToolRuntime([FunctionTool(ToolDefinition(name="hidden", description="Hidden"), never)], denied=["hidden"])
        for name in ("hidden", "missing"):
            self.assertEqual((await runtime.invoke(ToolCall(name=name))).status, "rejected")
        self.assertFalse(runtime.definitions())

    async def test_timeout_releases_only_after_confirmed_stop(self):
        for acknowledge in (True, False):
            with self.subTest(acknowledge=acknowledge):
                stops = []
                async def run(args, context):
                    await asyncio.Event().wait()
                async def stop(call_id, reason):
                    stops.append(call_id)
                    return acknowledge
                tool = FunctionTool(ToolDefinition(name="robot.move", description="Move", read_only=False,
                    resources=["robot:arm"], timeout_s=.02), run, stop)
                runtime = ToolRuntime([tool], stop_timeout_s=.02)
                request = ToolCall(name="robot.move")
                result = await runtime.invoke(request)
                self.assertEqual(result.status, "timed_out")
                self.assertEqual(stops, [request.id])
                self.assertEqual(runtime.control_locked, not acknowledge)
                self.assertEqual(bool(runtime.leases), not acknowledge)

    async def test_cancellation_waits_for_stop_and_revokes_context(self):
        entered, stopped = asyncio.Event(), asyncio.Event()
        contexts = []
        async def run(args, context):
            contexts.append(context)
            entered.set()
            await asyncio.Event().wait()
        async def stop(call_id, reason):
            stopped.set()
            return True
        tool = FunctionTool(ToolDefinition(name="robot.act", description="Act", read_only=False,
                                           resources=["body"]), run, stop)
        runtime = ToolRuntime([tool], stop_timeout_s=.02)
        task = asyncio.create_task(runtime.invoke(ToolCall(name="robot.act")))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped.is_set())
        self.assertFalse(runtime.leases)
        with self.assertRaises(asyncio.CancelledError):
            contexts[0].check_active()

    async def test_resource_conflicts_do_not_run_second_action(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def run(args, context):
            entered.set()
            await release.wait()
            return ToolResult()
        async def stop(call_id, reason):
            return True
        runtime = ToolRuntime([FunctionTool(ToolDefinition(name="move", description="Move",
            read_only=False, resources=["arm"]), run, stop)])
        pending = asyncio.create_task(runtime.invoke(ToolCall(name="move")))
        await entered.wait()
        result = await runtime.invoke(ToolCall(name="move"))
        self.assertEqual(result.status, "rejected")
        release.set()
        await pending

    async def test_wrong_output_is_an_error_not_a_successful_result(self):
        async def wrong(args, context):
            return ToolResult(data={"pose": "not a number"})
        runtime = ToolRuntime([FunctionTool(ToolDefinition(name="estimate", description="Estimate",
            output_schema={"type": "object", "properties": {"pose": {"type": "number"}}}), wrong)])
        self.assertEqual((await runtime.invoke(ToolCall(name="estimate"))).status, "error")

    async def test_failed_intent_log_prevents_action_dispatch(self):
        dispatched = []
        class BrokenLog:
            def write(self, event, **payload):
                raise OSError("Disk full")
        async def run(args, context):
            dispatched.append(context.call_id)
            return ToolResult()
        async def stop(call_id, reason):
            return True
        runtime = ToolRuntime([FunctionTool(ToolDefinition(name="move", description="Move",
            read_only=False, resources=["arm"]), run, stop)], log=BrokenLog())
        with self.assertRaises(OSError):
            await runtime.invoke(ToolCall(name="move"))
        await runtime.close()
        await asyncio.sleep(0)
        self.assertEqual(dispatched, [])
        self.assertFalse(runtime.leases)

    async def test_failed_completion_log_does_not_release_unconfirmed_action(self):
        class BrokenLog:
            def write(self, event, **payload):
                if event == "tool_finished":
                    raise OSError("Disk full")
        async def run(args, context):
            await asyncio.Event().wait()
        async def stop(call_id, reason):
            raise RuntimeError("Robot connection lost")
        runtime = ToolRuntime([FunctionTool(ToolDefinition(name="move", description="Move",
            read_only=False, resources=["arm"], timeout_s=.01), run, stop)], log=BrokenLog())
        with self.assertRaises(OSError):
            await runtime.invoke(ToolCall(name="move"))
        self.assertTrue(runtime.control_locked)
        self.assertTrue(runtime.leases)
        await runtime.close()

    def test_duplicate_and_unsafe_tool_definitions_fail_before_execution(self):
        async def run(args, context):
            return ToolResult()
        tool = FunctionTool(ToolDefinition(name="read", description="Read"), run)
        with self.assertRaises(ValueError):
            ToolRuntime([tool, tool])
        with self.assertRaises(ValueError):
            FunctionTool(ToolDefinition(name="move", description="Move", read_only=False), run)
