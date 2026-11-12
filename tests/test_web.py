import asyncio
from dataclasses import asdict
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from importlib.util import find_spec

GUI_AVAILABLE = all(find_spec(name) for name in ("httpx", "fastapi", "uvicorn"))
if GUI_AVAILABLE:
    import httpx
    from primitive_vla.interfaces.web.app import RobotProfile, create_app

from primitive_vla.contracts import Domain, Record, ResourceRef
from primitive_vla.memory import Memory
from primitive_vla.memory.organizer import MemoryOrganizer
from primitive_vla.planning import Decision
from primitive_vla.runtime.agent import Agent
from primitive_vla.skills import SkillStore
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.registry import ToolRegistry
from tests.support import Provider, ScriptedPlanner, spec


@unittest.skipUnless(GUI_AVAILABLE, "install primitive-vla[gui,test] to test the GUI API")
class WorkbenchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.app = create_app(workspace=self.directory.name)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://localhost")
        self.agent = None
        self.provider = None

    async def asyncTearDown(self):
        if self.provider:
            self.provider.confirms_stop = True
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.directory.cleanup()

    async def attach(self, planner, provider=None):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        registry, resources = ToolRegistry(), ResourceManager()
        resources.define_robot("r1", {"base"})
        if provider:
            self.provider = provider
            registry.register(spec("execution.test", Domain.EXECUTION, execution_mode="async",
                                   required_resources=("base",)), provider)
        self.agent = Agent(planner, registry, Memory(self.directory.name), SkillStore(), resources)
        self.app = create_app(self.agent, robots=(RobotProfile("r1", "test", "Test robot"),))
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://localhost")

    async def create_task(self, start=False):
        response = await self.client.post("/api/sessions", json={"goal": "Inspect the room", "robot_id": "r1", "start": start})
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    async def test_application_shutdown_cancels_background_memory_inference(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        started, cancelled = asyncio.Event(), asyncio.Event()

        class Model:
            async def generate(inner, request):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        memory = Memory(self.directory.name)
        memory.write(Record("s", "t", "planning", Domain.PLANNING, "note", "recorded",
                            {"title": "Note", "content": "Reusable observation"}, lifetime="long"))
        organizer = MemoryOrganizer(memory, Model(), interval=0.01)
        agent = Agent(ScriptedPlanner(), ToolRegistry(), memory, SkillStore(), ResourceManager())
        self.app = create_app(agent, organizer=organizer)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://localhost")
        await asyncio.wait_for(started.wait(), 1)
        await self.lifespan.__aexit__(None, None, None)
        self.assertTrue(cancelled.is_set())

    async def test_background_memory_failure_is_visible_and_not_silently_retried(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)

        class Model:
            async def generate(inner, request):
                raise ValueError("organization rejected")

        memory = Memory(self.directory.name)
        memory.write(Record("s", "t", "planning", Domain.PLANNING, "note", "recorded",
                            {"content": "Observation"}, lifetime="long"))
        organizer = MemoryOrganizer(memory, Model(), interval=0.01)
        agent = Agent(ScriptedPlanner(), ToolRegistry(), memory, SkillStore(), ResourceManager())
        self.app = create_app(agent, organizer=organizer)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://localhost")
        await asyncio.wait({self.app.state.console.maintenance}, timeout=1)
        status = (await self.client.get("/api/status")).json()["memory_maintenance"]
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["error"], "organization rejected")

    async def test_unconnected_app_saves_real_drafts_without_a_fake_planner(self):
        status = (await self.client.get("/api/status")).json()
        self.assertFalse(status["connected"])
        self.assertEqual(status["tools"], 0)
        session_id = await self.create_task()
        data = (await self.client.get(f"/api/sessions/{session_id}")).json()
        self.assertEqual(data["session"]["status"], "draft")
        self.assertEqual(data["session"]["decisions"], 0)
        self.assertEqual(data["records"][0]["event_type"], "task_created")
        self.assertEqual((await self.client.post(f"/api/sessions/{session_id}/resume")).status_code, 409)

    async def test_start_without_runtime_does_not_create_a_broken_session(self):
        response = await self.client.post("/api/sessions", json={"goal": "Move", "start": True})
        self.assertEqual(response.status_code, 409)
        self.assertEqual((await self.client.get("/api/sessions")).json()["sessions"], [])

    async def test_api_validates_input_at_the_boundary(self):
        for body in ({"goal": "   "}, {"goal": "Do it", "surprise": 1}):
            self.assertEqual((await self.client.post("/api/sessions", json=body)).status_code, 422)
        self.assertEqual((await self.client.get("/api/memory?limit=1000")).status_code, 422)
        self.assertEqual((await self.client.get("/api/sessions/missing")).status_code, 404)

    async def test_draft_is_loadable_after_runtime_attachment(self):
        session_id = await self.create_task()
        await self.attach(ScriptedPlanner(Decision("finish", {"outcome": "success", "message": "Done"})))
        response = await self.client.post(f"/api/sessions/{session_id}/resume?robot_id=r1")
        self.assertEqual(response.status_code, 200)
        await self.app.state.console.service.tasks[session_id]
        data = (await self.client.get(f"/api/sessions/{session_id}")).json()
        self.assertEqual(data["session"]["status"], "completed")
        self.assertEqual(data["session"]["decisions"], 1)

    async def test_replies_continue_the_existing_runtime_session(self):
        await self.attach(ScriptedPlanner(
            Decision("ask_user", {"question": "Choose a target"}),
            Decision("finish", {"outcome": "success", "message": "Received"}),
        ))
        session_id = await self.create_task(start=True)
        await self.app.state.console.service.tasks[session_id]
        response = await self.client.post(f"/api/replies/{session_id}", json={"answer": "Point B", "payload": {"xy": [2, 3]}})
        self.assertEqual(response.status_code, 200)
        await self.app.state.console.service.tasks[session_id]
        record = self.agent.memory.search(filters={"event_type": "user_input"})[0]
        self.assertEqual(record.payload["xy"], [2, 3])
        self.assertEqual(self.agent.sessions[session_id].status, "completed")

    async def test_pause_keeps_running_action_and_cancel_confirms_stop(self):
        provider = Provider(blocked=True)
        await self.attach(ScriptedPlanner(
            Decision("call_tool", {"name": "execution.test", "arguments": {"value": 1}}), Decision("wait"),
        ), provider)
        session_id = await self.create_task(start=True)
        await provider.started.wait()
        response = await self.client.post(f"/api/sessions/{session_id}/pause")
        self.assertEqual(response.json()["session"]["status"], "paused")
        self.assertIn("base", response.json()["occupied"])
        response = await self.client.post(f"/api/sessions/{session_id}/cancel")
        self.assertEqual(response.json()["session"]["status"], "cancelled")
        self.assertEqual(response.json()["occupied"], {})

    async def test_desktop_close_refuses_unconfirmed_motion(self):
        provider = Provider(blocked=True, confirms_stop=False)
        await self.attach(ScriptedPlanner(
            Decision("call_tool", {"name": "execution.test", "arguments": {"value": 1}}), Decision("wait"),
        ), provider)
        session_id = await self.create_task(start=True)
        await provider.started.wait()
        self.assertEqual((await self.client.get("/api/desktop/activity")).json()["active_calls"], 1)
        response = await self.client.post("/api/desktop/prepare-close")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.agent.sessions[session_id].status, "stopping")
        self.assertIn("base", self.agent.router.resources.occupied("r1"))
        provider.confirms_stop = True
        response = await self.client.post("/api/desktop/prepare-close")
        self.assertEqual(response.json(), {"ready": True})
        self.assertEqual(self.agent.router.resources.occupied("r1"), {})

    async def test_memory_search_preserves_records_and_domains(self):
        await self.create_task()
        memory = self.app.state.console.memory
        evidence = memory.write(Record("s", "t", "verification.external", Domain.VERIFICATION,
                                        "verification", "completed", {"conclusion": "uncertain", "reason": "occluded"}))
        response = await self.client.get("/api/memory?query=occluded&domain=verification")
        self.assertEqual(response.json()["records"][0]["id"], evidence.id)
        self.assertEqual((await self.client.get(f"/api/records/{evidence.id}")).json()["payload"]["conclusion"], "uncertain")

    async def test_timeline_pages_and_details_remain_readable_after_forgetting(self):
        session_id = await self.create_task()
        memory = self.app.state.console.memory
        note = memory.write(Record(session_id, "t", "planning", Domain.PLANNING,
                                    "note", "recorded", {"content": "original"}))
        memory.update(note.id, {"payload": {"content": "edited"}}, session_id=session_id)
        current = (await self.client.get(f"/api/records/{note.id}")).json()
        original = (await self.client.get(f"/api/evidence/{note.id}")).json()
        self.assertEqual(current["payload"]["content"], "edited")
        self.assertEqual(original["payload"]["content"], "original")
        memory.delete(note.id, session_id=session_id)
        for index in range(65):
            memory.write(Record(session_id, "t", "planning", Domain.PLANNING,
                                "decision", "recorded", {"action": "memory_search"},
                                timestamp=note.timestamp + index + 1))
        recent = (await self.client.get(f"/api/sessions/{session_id}")).json()
        self.assertTrue(recent["has_more"])
        self.assertEqual(len(recent["records"]), 60)
        older = (await self.client.get(f"/api/sessions/{session_id}/history?offset=60")).json()["records"]
        self.assertEqual(len(older), 7)
        self.assertIn(note.id, {record["id"] for record in older})
        self.assertEqual((await self.client.get(f"/api/records/{note.id}")).status_code, 404)
        self.assertEqual((await self.client.get(f"/api/evidence/{note.id}")).json(), original)
        self.assertNotIn(note.id, {record["id"] for record in
                         (await self.client.get("/api/memory")).json()["records"]})
        decision_id = recent["records"][-1]["id"]
        self.assertEqual((await self.client.get(f"/api/evidence/{decision_id}")).status_code, 200)

    async def test_resource_content_serves_only_indexed_local_data(self):
        memory = self.app.state.console.memory
        local = memory.put_resource(b"image bytes", "image/png")
        self.assertEqual((await self.client.get(f"/api/resources/{local.id}/content")).content, b"image bytes")
        external = ResourceRef("file:///etc/passwd", "text/plain")
        memory.register_resource(external)
        self.assertEqual((await self.client.get(f"/api/resources/{external.id}/content")).status_code, 409)
        html = memory.put_resource(b"<script>bad()</script>", "text/html")
        self.assertIn("attachment", (await self.client.get(f"/api/resources/{html.id}/content")).headers["content-disposition"])

    async def test_cross_origin_browser_control_is_rejected(self):
        response = await self.client.post("/api/sessions", json={"goal": "Move"}, headers={"Origin": "https://unrelated.invalid"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual((await self.client.get("/api/sessions")).json()["sessions"], [])

    async def test_desktop_token_is_required_without_exposing_it_in_status(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"PRIMITIVE_DESKTOP_TOKEN": "test-token"}):
            app = create_app(workspace=directory)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
                    self.assertEqual((await client.get("/api/status")).status_code, 401)
                    response = await client.get("/api/status", headers={"X-Primitive-Token": "test-token"})
                    self.assertEqual(response.status_code, 200)
                    self.assertNotIn("test-token", response.text)

    async def test_sse_reconnect_starts_with_a_complete_current_snapshot(self):
        session_id = await self.create_task()
        endpoint = next(route.endpoint for route in self.app.routes if getattr(route, "path", "") == "/api/sessions/{session_id}/stream")

        class Request:
            async def is_disconnected(self):
                return False

        response = await endpoint(session_id, Request())
        event = await anext(response.body_iterator)
        self.assertIn("event: snapshot", event)
        self.assertIn(session_id, event)
        self.assertIn("task_created", event)
        await response.body_iterator.aclose()

    async def test_tool_registry_projects_real_registered_contracts(self):
        await self.attach(ScriptedPlanner(), Provider())
        tools = (await self.client.get("/api/tools")).json()["tools"]
        self.assertEqual(tools[0]["name"], "execution.test")
        self.assertEqual(tools[0]["required_resources"], ["base"])
        self.assertEqual((await self.client.get("/api/skills")).json(), {"skills": []})
