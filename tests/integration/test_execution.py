import asyncio
import unittest

from primitive_vla.execution.contracts import ExecutionUpdate
from primitive_vla.execution.runtime import ExecutionManager
from primitive_vla.execution.tool import ExecutionTool
from primitive_vla.state.contracts import Observation, StateClaim
from primitive_vla.state.journal import StateJournal
from primitive_vla.tools import FunctionTool, ToolCall, ToolDefinition, ToolResult


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_during_timeout_stop_preserves_the_confirmation(self):
        stopping, acknowledged = asyncio.Event(), asyncio.Event()
        async def run(args, context):
            await asyncio.Event().wait()
        async def stop(*args):
            stopping.set()
            await acknowledged.wait()
            return True
        runtime = ExecutionManager([FunctionTool(ToolDefinition(name="move", description="move",
            read_only=False, resources=["arm"], timeout_s=.01), run, stop)])
        call = ToolCall(name="move")
        pending = asyncio.create_task(runtime.invoke(call))
        try:
            await asyncio.wait_for(stopping.wait(), 1)
            pending.cancel()
            await asyncio.sleep(0)
            self.assertFalse(pending.done())
            acknowledged.set()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            self.assertFalse(runtime.control_locked)
            self.assertFalse(runtime.leases)
            self.assertTrue(runtime.receipts[call.id].control_released)
        finally:
            acknowledged.set()
            await runtime.close()

    async def test_explicit_unknown_control_overrides_legacy_default(self):
        async def call(args, ctx):
            return ToolResult(execution=ExecutionUpdate(phase="unknown"))
        async def stop(*args):
            return False
        runtime = ExecutionManager([FunctionTool(ToolDefinition(name="move", description="move",
            read_only=False, resources=["arm"]), call, stop)])
        result = await runtime.invoke(ToolCall(name="move"))
        self.assertEqual(result.execution.control, "unknown")
        self.assertFalse(result.control_released)
        self.assertTrue(runtime.control_locked)
        self.assertEqual((await runtime.invoke(ToolCall(name="move"))).status, "rejected")

    async def test_async_backend_is_polled_and_stop_request_is_not_confirmation(self):
        class Backend:
            async def start(self, args, context):
                return ExecutionUpdate(phase="accepted", control="held")
            async def inspect(self, call_id, context):
                return ExecutionUpdate(phase="running", control="held")
            async def stop(self, call_id, reason):
                return ExecutionUpdate(phase="running", control="held", detail="Cancel queued")
        definition = ToolDefinition(name="policy", description="policy", read_only=False,
                                    resources=["robot"], timeout_s=.02)
        runtime = ExecutionManager([ExecutionTool(definition, Backend(), poll_s=.001)])
        result = await runtime.invoke(ToolCall(name="policy"))
        self.assertEqual(result.status, "timed_out")
        self.assertTrue(runtime.control_locked)

    async def test_dispatch_invalidates_state_but_rejected_call_does_not(self):
        state = StateJournal()
        state.record_result(ToolCall(id="camera", name="camera"), ToolResult(
            observations=[Observation(id="obs", sensor="camera", captured_at=state.clock())]))
        ref = state.record_claims([StateClaim(subject="cup", property="holding.v1", value=True,
                                            evidence=["obs"])], "model")[0]
        async def call(args, context):
            self.assertEqual(state.validity(ref), "stale")
            return ToolResult(execution=ExecutionUpdate(phase="finished", control="released"))
        async def stop(*args):
            return True
        runtime = ExecutionManager([FunctionTool(ToolDefinition(name="move", description="move",
            read_only=False, resources=["arm"]), call, stop)], state=state)
        await runtime.invoke(ToolCall(name="missing"))
        self.assertEqual(state.validity(ref), "current")
        result = await runtime.invoke(ToolCall(name="move"))
        self.assertEqual(result.execution.phase, "finished")
        self.assertEqual(len([r for r in state.records.values() if r["kind"] == "execution"]), 2)

    async def test_read_can_run_while_unrelated_action_owns_control(self):
        entered, done = asyncio.Event(), asyncio.Event()
        async def move(args, context):
            entered.set()
            await done.wait()
            return ToolResult()
        async def read(args, context):
            return ToolResult(data={"sensor": 1})
        async def stop(*args):
            return True
        runtime = ExecutionManager([
            FunctionTool(ToolDefinition(name="move", description="move", read_only=False,
                                       resources=["arm"]), move, stop),
            FunctionTool(ToolDefinition(name="read", description="read"), read)])
        task = asyncio.create_task(runtime.invoke(ToolCall(name="move")))
        await entered.wait()
        self.assertEqual((await runtime.invoke(ToolCall(name="read"))).data["sensor"], 1)
        done.set()
        await task

    async def test_malformed_state_result_is_feedback_without_partial_adoption(self):
        state = StateJournal()
        async def read(args, context):
            return ToolResult(claims=[StateClaim(subject="cup", property="holding.v1", value=True,
                                                evidence=["invented"])])
        runtime = ExecutionManager([FunctionTool(ToolDefinition(name="read", description="read"), read)], state=state)
        call = ToolCall(name="read")
        result = await runtime.invoke(call)
        self.assertEqual(result.status, "error")
        state.record_result(call, result)
        self.assertFalse(state.query())
