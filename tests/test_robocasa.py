"""Provider contract checks. FakeScene is a test double, never a runtime backend."""
import asyncio
import ast
import base64
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import patch

from primitive_vla.contracts import Domain, Failure, Record
from primitive_vla.events import EventBus
from primitive_vla.memory import Memory
from tests.robocasa_support import RoboCasa, RoboCasaConfig, MotionCancelled, ROBOT_TYPE, environment_module
from runtime.backend import NativeScene
from primitive_vla.skills import SkillStore
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.hooks import Hooks
from primitive_vla.tools.registry import ToolRegistry
from primitive_vla.tools.registry.loading import register_tools
from primitive_vla.tools.router import ToolRouter
from primitive_vla.tools.state.views import ViewFrame

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jK1sAAAAASUVORK5CYII=")


class FakeScene:
    def __init__(self, config, publish, preview):
        self.publish = publish
        self.preview = preview
        self.steps = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.threads = [threading.get_ident()]
        self.invocations = []
        self.success = False
        self.fail_motion = False
        self.fail_verification = False
        self.fail_preview = False
        self.renders = []
        self.closed = False
        self.projected = []

        def project(camera, row, column):
            self.projected.append((camera, row, column))
            return SimpleNamespace(tolist=lambda: [1, 2, 3])

        self.env = SimpleNamespace(world_xyz_at=project)
        self.publish(self.observation())

    def check_cancelled(self):
        if self.cancel_event.is_set():
            raise MotionCancelled()

    def observation(self):
        return {"observed_at": time.time(), "simulation_steps": self.steps,
                "task_language": "test-only goal", "robot_state": {"base": [self.steps, 0, 0]}}

    def invoke(self, operation, arguments, cancelled):
        self.threads.append(threading.get_ident())
        self.invocations.append(operation)
        if cancelled.is_set():
            raise MotionCancelled()
        if operation == "capture_image":
            return ({**self.observation(), "camera": arguments.get("camera", "agentview"),
                     "width": 1, "height": 1}, [("rgb", PNG, "image/png")])
        if operation == "localize_pixel":
            return NativeScene.invoke(self, operation, arguments, cancelled)
        if operation == "verify_task":
            if self.fail_verification:
                raise RuntimeError("predicate unavailable")
            return ({**self.observation(), "predicate": self.success,
                     "task_progress": {}, "basis": "simulator_ground_truth"}, [])
        for _ in range(arguments.get("steps", 1)):
            if cancelled.is_set():
                raise MotionCancelled()
            self.started.set()
            self.release.wait()  # Model a synchronous step that must drain before stop is confirmed.
            self.steps += 1
            self.publish(self.observation())
            self.preview(self)
        return ({**self.observation(), "native_result": {"ok": not self.fail_motion}}, [])

    def render_view(self, view_id):
        self.threads.append(threading.get_ident())
        self.renders.append((view_id, self.steps))
        if self.fail_preview:
            raise RuntimeError("camera unavailable")
        return ViewFrame(str(len(self.renders)), time.time(), "image/png", PNG, 1, 1,
                         {"simulation_steps": self.steps})

    def close(self):
        self.threads.append(threading.get_ident())
        self.closed = True


class RoboCasaModuleTests(unittest.TestCase):
    def test_robot_runtime_contains_implementations_without_tool_catalogs(self):
        root = Path(__file__).parents[1] / 'workspace/robocasa/runtime'
        for path in root.glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.assertNotIn(node.name, {'register', 'declarations'}, path)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotEqual(node.func.id, 'ToolSpec', path)


class RoboCasaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.memory = Memory(self.directory.name)
        self.config = RoboCasaConfig(workdir=Path(self.directory.name) / "native")
        self.factory = patch.object(environment_module, "NativeScene", FakeScene)
        self.factory.start()
        self.integration = await RoboCasa.open(self.config, self.memory)
        self.scene = self.integration._scene
        self.registry, self.resources, self.skills = ToolRegistry(), ResourceManager(), SkillStore()
        self.resources.define_robot(self.config.robot_id, {"simulation"})
        self.skills.load(Path(__file__).parents[1] / "workspace/robocasa/skills")
        register_tools(self.registry, self.skills.tool_definitions(), self.integration)
        self.router = ToolRouter(self.registry, self.resources, Hooks(self.memory, EventBus()),
                                 cancel_timeout=0.02)

    async def asyncTearDown(self):
        self.scene.release.set()
        # Wait for already submitted steps before closing the runtime.
        pending = [work.future for work in self.integration._works.values()
                   if work.future is not None]
        if pending:
            await asyncio.wait(pending)
        await self.router.close()
        await self.integration.close()
        self.memory.close()
        self.factory.stop()
        self.directory.cleanup()

    async def invoke(self, name, arguments=None, **changes):
        options = {"session_id": "s", "task_id": "t", "robot_id": self.config.robot_id,
                   "robot_type": ROBOT_TYPE, **changes}
        return await self.router.invoke(name, arguments or {}, **options)

    async def ended(self, call):
        await asyncio.wait_for(call.done.wait(), 1)
        return call

    async def test_registration_and_input_error_do_not_advance_physics(self):
        catalog = {entry.name: entry for entry in self.registry.catalog()}
        self.assertEqual(catalog["execution.robocasa.move_base"].required_resources, ("simulation",))
        self.assertNotIn("execution.robocasa.reset", catalog)
        self.assertIn("state.robocasa.observe", {s["id"] for s in self.skills.catalog()})
        rejected = await self.invoke("execution.robocasa.move_base", {"steps": 0})
        self.assertEqual(rejected.error.kind, Failure.INTERFACE)
        pixel = {"row": 0, "column": 0, "image": "unknown-image"}
        for invalid in ({}, {"column": -1}, {"episode_id": "old"}):
            rejected = await self.invoke("state.robocasa.localize_pixel", {**pixel, **invalid})
            self.assertEqual(rejected.error.kind, Failure.INTERFACE)
        self.assertEqual(self.scene.invocations, [])

    async def test_camera_bytes_are_resource_refs_in_memory(self):
        result = await self.invoke("state.robocasa.capture_image", {"camera": "navview"})
        self.assertEqual(result.status, "completed")
        image_id = result.result.resource_refs[0].id
        self.assertEqual(self.memory.read_resource(image_id), PNG)
        self.assertEqual(result.result.payload, {})
        self.assertEqual(self.memory.get_resource(image_id).metadata, {
            "camera": "navview", "width": 1, "height": 1, "simulation_steps": 0,
            "episode_id": self.integration.episode_id})
        self.assertEqual(self.integration._works[result.id].future.result()[1], [])

    async def test_pixel_localization_uses_captured_camera_and_rejects_stale_image(self):
        captured = await self.invoke("state.robocasa.capture_image", {"camera": "wrist"})
        image = captured.result.resource_refs[0]
        args = {"image": image.id, "row": 0, "column": 0}
        located = await self.invoke("state.robocasa.localize_pixel", args)
        self.assertEqual(located.status, "completed")
        self.assertEqual(located.result.payload["point_world"], [1, 2, 3])
        self.assertEqual(self.scene.projected, [("wrist", 0, 0)])
        bounds = await self.invoke("state.robocasa.localize_pixel", {**args, "row": 1})
        self.assertEqual(bounds.error.kind, Failure.INTERFACE)
        await self.ended(await self.invoke("execution.robocasa.move_base"))
        stale = await self.invoke("state.robocasa.localize_pixel", args)
        self.assertEqual(stale.error.kind, Failure.PRECONDITION)
        self.assertIn("stale", str(stale.error))
        self.assertEqual(len(self.scene.projected), 1)
        foreign = self.memory.put_resource(PNG, "image/png", metadata={**image.metadata, "episode_id": "other"})
        rejected = await self.invoke("state.robocasa.localize_pixel", {**args, "image": foreign.id})
        self.assertEqual(rejected.error.kind, Failure.INTERFACE)

    async def test_preview_is_read_only_and_stops_rendering_without_subscribers(self):
        views = self.integration.views
        records = self.memory.search()
        stream = views.subscribe_view("agentview")
        frame = await asyncio.wait_for(anext(stream), 1)
        self.assertEqual(frame.data, PNG)
        self.assertEqual(self.scene.steps, 0)
        self.assertEqual(self.memory.search(), records)
        self.assertEqual(self.resources.occupied(self.config.robot_id), {})
        await stream.aclose()
        await views._pump
        renders = len(self.scene.renders)
        action = await self.ended(await self.invoke("execution.robocasa.move_base"))
        self.assertEqual(action.status, "completed")
        self.assertEqual(len(self.scene.renders), renders)

    async def test_preview_subscribers_share_captures_and_skip_old_frames(self):
        views = self.integration.views
        views._interval = 0.005
        first = views.subscribe_view("agentview")
        second = views.subscribe_view("agentview")
        a, b = await asyncio.wait_for(asyncio.gather(anext(first), anext(second)), 1)
        self.assertEqual(a.id, b.id)
        await asyncio.sleep(0.05)
        latest = await anext(first)
        self.assertGreater(int(latest.id), int(a.id) + 1)
        await first.aclose()
        self.assertGreater(int((await anext(second)).id), int(b.id))
        await second.aclose()
        await views._pump

    async def test_preview_updates_during_action_and_failure_does_not_abort_control(self):
        views = self.integration.views
        views._interval = 0  # Exercise the per-step hook without timer-based test waits.
        stream = views.subscribe_view("agentview")
        await anext(stream)
        self.scene.release.clear()
        action = await self.invoke("execution.robocasa.move_base", {"steps": 2})
        await asyncio.to_thread(self.scene.started.wait)
        self.scene.release.set()
        await self.ended(action)
        frame = await asyncio.wait_for(anext(stream), 1)
        self.assertGreater(frame.metadata["simulation_steps"], 0)
        self.scene.fail_preview = True
        action = await self.ended(await self.invoke("execution.robocasa.move_base"))
        with self.assertRaisesRegex(RuntimeError, "camera unavailable"):
            await anext(stream)
        self.assertEqual(action.status, "completed")
        self.assertEqual(len(set(self.scene.threads)), 1)

    async def test_execution_complete_and_verification_failure_are_separate(self):
        action = await self.ended(await self.invoke("execution.robocasa.move_base"))
        self.assertEqual(action.status, "completed")
        self.assertIsNone(action.result.verification)
        self.assertEqual(action.result.payload, {})
        verification = await self.invoke("verification.robocasa.task", {"related_call_id": action.id})
        self.assertEqual(verification.status, "completed")
        self.assertEqual(verification.result.verification.conclusion, "failure")
        self.assertEqual(verification.result.verification.related_call_id, action.id)
        self.assertTrue(verification.result.verification.evidence)
        self.assertEqual(set(verification.result.payload), {"task_progress", "basis"})
        records = self.memory.search(filters={"call_id": verification.id, "event_type": "verification"})
        self.assertEqual(records[0].payload["result"]["verification"]["conclusion"], "failure")
        self.scene.success = True
        success = await self.invoke("verification.robocasa.task")
        self.assertEqual(success.result.verification.conclusion, "success")

    async def test_conflict_rejected_and_cached_observation_available_during_motion(self):
        self.scene.release.clear()
        action = await self.invoke("execution.robocasa.move_base", {"steps": 2})
        await asyncio.to_thread(self.scene.started.wait)
        conflict = await self.invoke("execution.robocasa.release")
        self.assertEqual(conflict.error.kind, Failure.RESOURCE)
        observed = await asyncio.wait_for(self.invoke("state.robocasa.observe"), 0.2)
        self.assertEqual(observed.result.payload["simulation_steps"], 0)
        self.scene.release.set()
        await self.ended(action)
        observed = await self.invoke("state.robocasa.observe")
        self.assertEqual(observed.result.payload["simulation_steps"], 2)

    async def test_cancel_keeps_lock_until_in_flight_step_returns(self):
        self.scene.release.clear()
        action = await self.invoke("execution.robocasa.move_base", {"steps": 20})
        await asyncio.to_thread(self.scene.started.wait)
        await self.router.cancel(action.id)
        self.assertEqual(action.status, "stop_unconfirmed")
        self.assertEqual(self.resources.occupied(self.config.robot_id), {"simulation": action.id})
        self.assertFalse(self.integration._works[action.id].future.cancelled())
        self.scene.release.set()
        await self.ended(action)
        self.assertEqual(action.status, "cancelled")
        self.assertEqual(self.scene.steps, 1)
        self.assertEqual(self.resources.occupied(self.config.robot_id), {})

    async def test_native_failure_releases_only_after_worker_ended(self):
        self.scene.fail_motion = True
        action = await self.ended(await self.invoke("execution.robocasa.move_base"))
        self.assertEqual(action.status, "failed")
        self.assertEqual(action.error.kind, Failure.EXECUTION)
        self.assertFalse(action.error.evidence["native_result"]["ok"])
        self.assertTrue(self.integration._works[action.id].future.done())
        self.assertEqual(self.resources.occupied(self.config.robot_id), {})

    async def test_bad_or_foreign_verification_link_rejected(self):
        action = await self.ended(await self.invoke("execution.robocasa.move_base"))
        foreign = await self.invoke("verification.robocasa.task", {"related_call_id": action.id},
                                    session_id="other")
        self.assertEqual(foreign.error.kind, Failure.INTERFACE)
        missing = await self.invoke("verification.robocasa.task", {"related_call_id": "missing"})
        self.assertEqual(missing.error.kind, Failure.INTERFACE)

    async def test_verification_exception_is_not_a_false_predicate(self):
        self.scene.fail_verification = True
        result = await self.invoke("verification.robocasa.task")
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.result)
        self.assertIn("predicate unavailable", str(result.error))

    async def test_bound_robot_cannot_be_bypassed_with_other_robot_id(self):
        result = await self.invoke("state.robocasa.observe", robot_id="other")
        self.assertEqual(result.error.kind, Failure.PERMISSION)
        self.resources.define_robot("other", {"simulation"})
        rejected = await self.ended(await self.invoke("execution.robocasa.move_base", robot_id="other"))
        self.assertEqual(rejected.status, "failed")
        self.assertEqual(rejected.error.kind, Failure.PERMISSION)
        self.assertEqual(self.resources.occupied("other"), {})
        self.assertEqual(self.scene.invocations, [])

    async def test_no_implicit_reset_after_reopen_and_no_old_session_replay(self):
        await self.invoke("state.robocasa.observe")
        await self.integration.close()
        with self.assertRaisesRegex(RuntimeError, "cannot restore"):
            await RoboCasa.open(self.config, self.memory)
        new = await RoboCasa.open(self.config, self.memory, new_episode=True)
        registry, resources, skills = ToolRegistry(), ResourceManager(), SkillStore()
        resources.define_robot(self.config.robot_id, {"simulation"})
        skills.load(Path(__file__).parents[1] / "workspace/robocasa/skills")
        register_tools(registry, skills.tool_definitions(), new)
        router = ToolRouter(registry, resources, Hooks(self.memory, EventBus()))
        try:
            rejected = await router.invoke("state.robocasa.observe", {}, session_id="s", task_id="t",
                                           robot_id=self.config.robot_id, robot_type=ROBOT_TYPE)
            self.assertEqual(rejected.error.kind, Failure.PRECONDITION)
            rejected_action = await router.invoke(
                "execution.robocasa.move_base", {}, session_id="s", task_id="t",
                robot_id=self.config.robot_id, robot_type=ROBOT_TYPE,
            )
            await asyncio.wait_for(rejected_action.done.wait(), 1)
            self.assertEqual(rejected_action.status, "failed")
            self.assertEqual(resources.occupied(self.config.robot_id), {})
        finally:
            await router.close()
            await new.close()

    async def test_unresolved_calls_block_even_explicit_new_episode(self):
        self.memory.write(Record("s", "t", "execution.robocasa.move_base", Domain.EXECUTION,
                                 "tool_started", "running", {"robot_id": self.config.robot_id,
                                                             "required_resources": ["simulation"]},
                                 call_id="lost-call"))
        with self.assertRaisesRegex(RuntimeError, "unresolved calls"):
            await RoboCasa.open(self.config, self.memory, new_episode=True)

    async def test_native_operations_and_cleanup_keep_thread_affinity(self):
        await self.invoke("state.robocasa.capture_image")
        await self.ended(await self.invoke("execution.robocasa.move_base"))
        await self.integration.close()
        self.assertEqual(len(set(self.scene.threads)), 1)
        self.assertNotEqual(self.scene.threads[0], threading.get_ident())
        self.assertTrue(self.scene.closed)
