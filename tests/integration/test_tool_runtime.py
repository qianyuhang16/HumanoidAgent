"""Composition and frontend integration through public extension interfaces."""

import asyncio
import base64
import json
from pathlib import Path
import tempfile
import unittest

import httpx

from primitive_vla.agent.contracts import FinalAnswer, ModelReply
from primitive_vla.config.loader import load_config, save_config
from primitive_vla.config.schema import AdapterSpec, AppConfig
from primitive_vla.runtime.session import Runtime
from primitive_vla.runtime.service import TaskService, TaskConflict
from primitive_vla.storage.events import EventLog
from primitive_vla.storage.tasks import TaskStore
from primitive_vla.tools import FunctionTool, ToolCall, ToolDefinition, ToolResult
from primitive_vla.web.app import create_app


class TestModel:
    def __init__(self, options, context):
        self.options, self.turn, self.closed = options, 0, False

    async def step(self, context, tools):
        self.turn += 1
        if self.turn == 1:
            return ModelReply(calls=[ToolCall(name=self.options.get("tool", "robot.observe"))])
        return ModelReply(final=FinalAnswer(status="completed", reason="Observed actual tool output"))

    async def close(self):
        self.closed = True


class CameraTools:
    def __init__(self, options, context):
        self.tools = [FunctionTool(ToolDefinition(name="camera.observe", description="Observe image"), self.observe)]
        self.closed = False

    async def observe(self, args, context):
        return ToolResult(data={"label": "actual image"}, images={"camera": "frame.png"})

    async def close(self):
        self.closed = True


class StalledTools:
    def __init__(self, options, context):
        self.entered = asyncio.Event()
        self.tools = [FunctionTool(ToolDefinition(name="robot.stall", description="Stalled fake action",
            read_only=False, resources=["body"]), self.run, self.stop)]

    async def run(self, args, context):
        self.entered.set()
        await asyncio.Event().wait()

    async def stop(self, call_id, reason):
        return False

    async def close(self):
        pass


def locator_one(options, context):
    async def call(args, context):
        return ToolResult(data={"provider": "one", "position": [0.1, 0.2, 0.3]})
    return FunctionTool(ToolDefinition(name="internal.one", description="Locate"), call)


def locator_two(options, context):
    async def call(args, context):
        return ToolResult(data={"provider": "two", "position": [0.1, 0.2, 0.3]})
    return FunctionTool(ToolDefinition(name="internal.two", description="Locate"), call)


class ToolRuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_replace_model_tool_by_configuration_preserves_public_name(self):
        for adapter, expected in (("locator_one", "one"), ("locator_two", "two")):
            with self.subTest(adapter=adapter), tempfile.TemporaryDirectory() as tmp:
                cfg = AppConfig(model=AdapterSpec(adapter=__name__ + ":TestModel", options={"tool": "perception.locate"}),
                                tool_providers=[], tools={"perception.locate": AdapterSpec(adapter=__name__ + ":" + adapter)})
                runtime = Runtime(cfg, Path(tmp), EventLog())
                try:
                    report = await runtime.run("Locate the target")
                    self.assertEqual(report["results"][0]["tool"], "perception.locate")
                    self.assertEqual(report["results"][0]["data"]["provider"], expected)
                finally:
                    await runtime.close()

    async def test_cancelled_action_with_unconfirmed_stop_is_persisted_and_blocks_next_task(self):
        cfg = AppConfig(model=AdapterSpec(adapter=__name__ + ":TestModel", options={"tool": "robot.stall"}),
                        tool_providers=[AdapterSpec(adapter=__name__ + ":StalledTools")])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            save_config(cfg, path)
            service = TaskService(Path(tmp), path)
            try:
                task = await service.start("Move")
                async def entered():
                    while not service.runtime.started:
                        await asyncio.sleep(0)
                    await service.runtime.resources[1].entered.wait()
                await asyncio.wait_for(entered(), 1)
                await service.cancel(task["id"])
                await asyncio.wait_for(service.worker, 1)
                report = service.get_task(task["id"])["report"]
                self.assertEqual(report["status"], "blocked")
                self.assertTrue(report["control_locked"])
                self.assertEqual(report["results"][-1]["status"], "cancelled")
                with self.assertRaises(TaskConflict):
                    await service.start("Try again")
            finally:
                await service.close()

    async def test_full_model_http_round_trip_includes_tool_schema_history_and_image(self):
        requests = []
        def server(request):
            body = json.loads(request.content)
            requests.append(body)
            reply = {"calls": [{"name": "camera.observe", "arguments": {}}], "final": None} if len(requests) == 1 else {
                "calls": [], "final": {"status": "completed", "reason": "Actual image reviewed", "evidence": []}}
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})
        cfg = AppConfig(model=AdapterSpec(adapter="primitive_vla.agent.model:JSONModel", options={"model": "test"}),
                        tool_providers=[AdapterSpec(adapter=__name__ + ":CameraTools")])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "media").mkdir()
            (root / "media/frame.png").write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY1cAAAAASUVORK5CYII="))
            runtime = Runtime(cfg, root, EventLog(), transport=httpx.MockTransport(server))
            try:
                report = await runtime.run("Inspect arbitrary imagery")
                self.assertEqual(report["status"], "completed")
                content = requests[1]["messages"][1]["content"]
                self.assertTrue(any(c["type"] == "image_url" and c["image_url"]["url"].startswith("data:image/png;base64,") for c in content))
                payload = json.loads(content[0]["text"])
                self.assertIn("camera.observe", [t["name"] for t in payload["tools"]])
                self.assertEqual(payload["history"][-1]["data"]["label"], "actual image")
            finally:
                await runtime.close()
            self.assertTrue(runtime.resources[1].closed)

    async def test_external_tool_transport_returns_results_without_core_changes(self):
        requests = []
        def server(request):
            payload = json.loads(request.content)
            requests.append(payload)
            return httpx.Response(200, json={"data": {"verdict": "unknown", "phase": "prediction"}})
        cfg = AppConfig(model=AdapterSpec(adapter=__name__ + ":TestModel", options={"tool": "future.assess"}),
            tool_providers=[AdapterSpec(adapter="primitive_vla.tools.http:HTTPTools", options={
                "endpoint": "http://world-model.test", "tools": [{"name": "future.assess", "description": "Predict an outcome"}]})])
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Runtime(cfg, Path(tmp), EventLog(), transport=httpx.MockTransport(server))
            try:
                report = await runtime.run("Assess the future")
                self.assertEqual(requests[0]["tool"], "future.assess")
                self.assertEqual(report["results"][0]["data"]["verdict"], "unknown")
            finally:
                await runtime.close()

    async def test_battery_demo_through_task_service_persists_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            save_config(load_config(Path("configs/battery-demo.yaml")), path)
            service = TaskService(Path(tmp), path)
            try:
                task = await service.start("装入电池，检查电源表，需要时调整方向。")
                await service.worker
                record = service.get_task(task["id"])
                self.assertEqual(record["status"], "completed")
                self.assertGreater(record["report"]["steps"], 4)
                trials = [r for r in record["report"]["results"] if r["tool"] == "battery.adjust"]
                self.assertEqual({r["arguments"]["side"] for r in trials}, {"left", "right"})
                self.assertTrue(record["state"]["history"])
            finally:
                await service.close()

    async def test_legacy_scene_skills_are_available_as_tools(self):
        cfg = AppConfig(model=AdapterSpec(adapter=__name__ + ":TestModel", options={"tool": "scene.observe"}),
                        tool_providers=[AdapterSpec(adapter="primitive_vla.tools.legacy:SceneTools")])
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Runtime(cfg, Path(tmp), EventLog())
            try:
                report = await runtime.run("查看场景")
                self.assertEqual(report["status"], "completed")
                self.assertIn("skill.GRASP", [t.name for t in runtime.tools.definitions()])
                self.assertIn("objects", report["results"][0]["data"])
            finally:
                await runtime.close()

    async def test_partial_start_failure_closes_previously_created_plugins(self):
        cfg = AppConfig(model=AdapterSpec(adapter=__name__ + ":TestModel"), tool_providers=[
            AdapterSpec(adapter=__name__ + ":CameraTools"), AdapterSpec(adapter=__name__ + ":CameraTools")])
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Runtime(cfg, Path(tmp), EventLog())
            with self.assertRaisesRegex(ValueError, "Duplicate tool"):
                await runtime.start()
            self.assertTrue(all(r.closed for r in runtime.resources))


class PersistenceAndWebTests(unittest.IsolatedAsyncioTestCase):
    def test_inflight_action_on_restart_locks_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.sqlite3"
            store = TaskStore(path)
            store.create("task", "move", {})
            store.event("task", {"event": "tool_started", "call": {"id": "action"}, "read_only": False})
            store.close()
            store = TaskStore(path)
            self.assertTrue(store.control_locked)
            self.assertEqual(store.get("task")["status"], "interrupted")
            store.close()

    async def test_web_serves_generic_console_and_versioned_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(Path(tmp))
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    config = (await client.get("/api/config")).json()["config"]
                    self.assertEqual(config["schema_version"], 2)
                    self.assertNotIn("scene", config)
                    self.assertEqual((await client.put("/api/config", json=config)).status_code, 200)
                    self.assertEqual((await client.get("/api/tools")).status_code, 200)
