import asyncio
from tempfile import TemporaryDirectory
import unittest

from primitive_vla.contracts import Domain, Failure, Record
from primitive_vla.events import EventBus
from primitive_vla.memory import Memory
from primitive_vla.tools.contracts import ToolResult, Verification
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.hooks import Hooks
from primitive_vla.tools.registry import ToolRegistry
from primitive_vla.tools.router import ToolRouter
from primitive_vla.tools.state import ObservationSink
from tests.support import Provider, spec


class ToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.memory = Memory(self.directory.name)
        self.events = EventBus()
        self.registry = ToolRegistry()
        self.resources = ResourceManager()
        for robot in ("r1", "r2"):
            self.resources.define_robot(robot, {"base", "arm", "hand"},
                                        {"whole_body": {"base", "arm", "hand"}})
        self.hooks = Hooks(self.memory, self.events)
        self.router = ToolRouter(self.registry, self.resources, self.hooks, cancel_timeout=0.02)

    async def asyncTearDown(self):
        for entry in self.registry._tools.values():
            entry.provider.confirms_stop = True
        await self.router.close()
        self.memory.close()
        self.directory.cleanup()

    async def invoke(self, name, arguments=None, **options):
        return await self.router.invoke(name, {"value": 1} if arguments is None else arguments,
                                        session_id="session", task_id="task", robot_type="robot",
                                        robot_id=options.pop("robot_id", "r1"), **options)

    async def test_input_rejection_is_recorded_without_provider_execution(self):
        provider = Provider()
        self.registry.register(spec(), provider)
        call = await self.invoke("state.inspect", {"value": "wrong"})
        self.assertEqual(call.error.kind, Failure.INTERFACE)
        self.assertEqual(provider.executions, 0)
        record = self.memory.search(filters={"call_id": call.id})[0]
        self.assertEqual(record.status, "failed")

    async def test_unknown_and_unauthorized_calls_are_feedback(self):
        unknown = await self.invoke("missing")
        self.assertEqual(unknown.error.kind, Failure.INTERFACE)
        provider = Provider()
        self.registry.register(spec(supported_robots=("another",)), provider)
        wrong_robot = await self.invoke("state.inspect")
        forbidden = await self.invoke("state.inspect", allowed_tools=())
        self.assertEqual(wrong_robot.error.kind, Failure.PERMISSION)
        self.assertEqual(forbidden.error.kind, Failure.PERMISSION)
        self.assertEqual(provider.executions, 0)

    async def test_domain_hooks_and_progress_share_call_identity(self):
        stages = []

        async def hook(stage, record):
            stages.append((stage, record.call_id))

        self.hooks.add(Domain.STATE, hook)
        self.registry.register(spec(), Provider())
        call = await self.invoke("state.inspect")
        self.assertEqual([stage for stage, _ in stages], ["before_call", "on_progress", "after_call"])
        self.assertEqual({call_id for _, call_id in stages}, {call.id})
        self.assertEqual(len(self.memory.search(filters={"call_id": call.id})), 3)

    async def test_alias_conflicts_are_atomic_and_camera_remains_available(self):
        provider = Provider(blocked=True)
        self.registry.register(spec("execution.move", Domain.EXECUTION,
                                    required_resources=("base",), execution_mode="async"), provider)
        self.registry.register(spec("execution.body", Domain.EXECUTION,
                                    required_resources=("whole_body",), execution_mode="async"), Provider())
        self.registry.register(spec(), Provider())
        first, second = await asyncio.gather(self.invoke("execution.move"), self.invoke("execution.body"))
        self.assertEqual(first.status, "running")
        self.assertEqual(second.error.kind, Failure.RESOURCE)
        self.assertEqual((await self.invoke("state.inspect")).status, "completed")
        other_robot = await self.invoke("execution.move", robot_id="r2")
        self.assertEqual(other_robot.status, "running")
        provider.release.set()
        await asyncio.wait_for(first.done.wait(), 1)
        await asyncio.wait_for(other_robot.done.wait(), 1)
        self.assertEqual(self.resources.occupied("r1"), {})

    async def test_unconfirmed_cancel_keeps_ownership_until_actual_completion(self):
        provider = Provider(blocked=True, confirms_stop=False)
        self.registry.register(spec("execution.move", Domain.EXECUTION,
                                    required_resources=("base",), execution_mode="async"), provider)
        call = await self.invoke("execution.move")
        await provider.started.wait()
        await self.router.cancel(call.id)
        self.assertEqual(call.status, "stop_unconfirmed")
        self.assertEqual(self.resources.occupied("r1"), {"base": call.id})
        self.assertEqual((await self.invoke("execution.move")).error.kind, Failure.RESOURCE)
        provider.release.set()
        await asyncio.wait_for(call.done.wait(), 1)
        self.assertEqual(call.status, "cancelled")
        self.assertEqual(self.resources.occupied("r1"), {})

    async def test_sync_timeout_returns_unconfirmed_without_releasing(self):
        provider = Provider(blocked=True, confirms_stop=False)
        self.registry.register(spec("execution.move", Domain.EXECUTION,
                                    required_resources=("base",), timeout=0.01), provider)
        call = await asyncio.wait_for(self.invoke("execution.move"), 1)
        self.assertEqual(call.status, "stop_unconfirmed")
        self.assertEqual(call.error.kind, Failure.TIMEOUT)
        self.assertIn("base", self.resources.occupied("r1"))
        provider.release.set()
        await asyncio.wait_for(call.done.wait(), 1)
        self.assertEqual(call.status, "failed")

    async def test_provider_exception_needs_physical_stop_confirmation(self):
        provider = Provider(confirms_stop=False, error=RuntimeError("controller disconnected"))
        self.registry.register(spec("execution.move", Domain.EXECUTION, required_resources=("arm",)), provider)
        call = await self.invoke("execution.move")
        self.assertEqual(call.status, "stop_unconfirmed")
        self.assertEqual(call.error.kind, Failure.EXECUTION)
        provider.confirms_stop = True
        await self.router.cancel(call.id)
        await self.router.cancel(call.id)
        self.assertEqual(call.status, "failed")
        self.assertEqual(provider.cancellations, 2)
        self.assertEqual(self.resources.occupied("r1"), {})

    async def test_cancel_ack_timeout_is_not_a_release(self):
        class SlowStop(Provider):
            async def cancel(self, context):
                if self.confirms_stop:
                    return True
                await asyncio.Event().wait()

        provider = SlowStop(blocked=True, confirms_stop=False)
        self.registry.register(spec("execution.move", Domain.EXECUTION, execution_mode="async",
                                    required_resources=("base",)), provider)
        call = await self.invoke("execution.move")
        await self.router.cancel(call.id)
        self.assertEqual(call.status, "stop_unconfirmed")
        self.assertIn("base", self.resources.occupied("r1"))

    async def test_verification_failure_is_not_execution_failure_or_success(self):
        self.registry.register(spec("execution.move", Domain.EXECUTION), Provider())
        action = await self.invoke("execution.move")
        self.registry.register(spec("verification.check", Domain.VERIFICATION), Provider(
            ToolResult({"value": 0}, verification=Verification("object", "failure", "no contact",
                                                               related_call_id=action.id))))
        verified = await self.invoke("verification.check")
        self.assertEqual(action.status, "completed")
        self.assertIsNone(action.result.verification)
        self.assertEqual(verified.status, "completed")
        self.assertEqual(verified.result.verification.conclusion, "failure")
        records = self.memory.search(filters={"event_type": "verification"})
        self.assertEqual(records[0].payload["result"]["verification"]["related_call_id"], action.id)

    async def test_invalid_return_schema_releases_completed_control(self):
        self.registry.register(spec("execution.move", Domain.EXECUTION, required_resources=("base",)),
                               Provider(ToolResult({"value": "wrong"})))
        call = await self.invoke("execution.move")
        self.assertEqual(call.error.kind, Failure.INTERFACE)
        self.assertEqual(self.resources.occupied("r1"), {})

    async def test_execution_cannot_publish_verification_conclusion(self):
        self.registry.register(spec("execution.move", Domain.EXECUTION), Provider(ToolResult(
            {"value": 1}, verification=Verification("object", "success", "guessed"))))
        self.assertEqual((await self.invoke("execution.move")).error.kind, Failure.INTERFACE)

    async def test_pre_call_hook_failure_releases_without_starting_provider(self):
        async def hook(stage, record):
            if stage == "before_call":
                raise RuntimeError("denied by extension")

        self.hooks.add(Domain.EXECUTION, hook)
        provider = Provider()
        self.registry.register(spec("execution.move", Domain.EXECUTION, required_resources=("base",)), provider)
        call = await self.invoke("execution.move")
        self.assertEqual(call.status, "failed")
        self.assertEqual(provider.executions, 0)
        self.assertEqual(self.resources.occupied("r1"), {})

    async def test_cancellation_during_before_hook_never_starts_motion(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def hook(stage, record):
            if stage == "before_call":
                entered.set()
                await release.wait()

        self.hooks.add(Domain.EXECUTION, hook)
        provider = Provider()
        self.registry.register(spec("execution.move", Domain.EXECUTION, required_resources=("base",)), provider)
        invoking = asyncio.create_task(self.invoke("execution.move"))
        await entered.wait()
        call = self.router.active("session")[0]
        await self.router.cancel(call.id)
        release.set()
        await invoking
        self.assertEqual(provider.executions, 0)
        self.assertEqual(call.status, "cancelled")

    async def test_observer_hook_error_does_not_change_physical_result(self):
        async def hook(stage, record):
            if stage == "after_call":
                raise RuntimeError("observer failed")

        self.hooks.add(Domain.STATE, hook)
        self.registry.register(spec(), Provider())
        self.assertEqual((await self.invoke("state.inspect")).status, "completed")
        self.assertEqual(len(self.memory.search(filters={"event_type": "hook_error"})), 1)

    async def test_observations_only_wake_on_significance_or_subscription(self):
        sink = ObservationSink(self.hooks)
        await sink.publish(session_id="s", task_id="t", source="camera", payload={"frame": 1})
        self.assertEqual(self.events.version("s"), 0)
        self.events.subscribe("s", ["camera"])
        await sink.publish(session_id="s", task_id="t", source="camera", payload={"frame": 2})
        self.assertEqual(self.events.version("s"), 1)
        self.events.subscribe("s", [])
        await sink.publish(session_id="s", task_id="t", source="localization", payload={}, significant=True)
        self.assertEqual(self.events.version("s"), 2)

    async def test_restore_quarantines_unfinished_calls_without_replay(self):
        provider = Provider()
        self.registry.register(spec("execution.move", Domain.EXECUTION, required_resources=("base",)), provider)
        self.memory.write(Record("session", "task", "execution.move", Domain.EXECUTION,
                                 "tool_started", "running", {"robot_id": "r1", "required_resources": ["base"]},
                                 call_id="old-call"))
        self.router.restore()
        self.assertEqual(provider.executions, 0)
        self.assertEqual(self.resources.occupied("r1"), {"base": "old-call"})
        self.assertEqual((await self.invoke("execution.move")).error.kind, Failure.RESOURCE)
        await self.router.cancel("old-call")
        self.assertEqual(self.resources.occupied("r1"), {})

    async def test_close_refuses_to_discard_unconfirmed_control(self):
        provider = Provider(blocked=True, confirms_stop=False)
        self.registry.register(spec("execution.move", Domain.EXECUTION, execution_mode="async",
                                    required_resources=("base",)), provider)
        await self.invoke("execution.move")
        with self.assertRaisesRegex(RuntimeError, "stop unconfirmed"):
            await self.router.close()

    def test_registry_rejects_invalid_schema_and_duplicate_names(self):
        self.registry.register(spec(), Provider())
        with self.assertRaises(ValueError):
            self.registry.register(spec(), Provider())
        with self.assertRaises(Exception):
            self.registry.register(spec("invalid", input_schema={"type": "bogus"}), Provider())
