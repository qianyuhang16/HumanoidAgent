import asyncio
import base64
from importlib.util import find_spec
import json
import os
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from primitive_vla.tools.state.views import ViewFrame, ViewSpec

GUI_AVAILABLE = all(find_spec(name) for name in ("httpx", "fastapi"))
if GUI_AVAILABLE:
    import httpx
    from fastapi import FastAPI
    from primitive_vla.interfaces.web.app import RobotProfile, create_app
    from primitive_vla.interfaces.web.views import view_routes


class TestCamera:
    """Finite test-only stream, so an in-process HTTP client can collect its body."""

    def __init__(self, error=None):
        self.error = error
        self.closed = False
        self.subscribed = []

    def list_views(self):
        return (ViewSpec("front", "Front camera", True), ViewSpec("wrist", "Wrist camera"))

    async def subscribe_view(self, view_id):
        self.subscribed.append(view_id)
        try:
            yield ViewFrame("frame-1", 123.5, "image/jpeg", b"test-image", 10, 20, {"step": 3})
            if self.error:
                raise self.error
        finally:
            self.closed = True


@unittest.skipUnless(GUI_AVAILABLE, "install primitive-vla[gui,test] for robot view transport")
class RobotViewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.camera = TestCamera()
        with patch.dict(os.environ, {"PRIMITIVE_DESKTOP_TOKEN": "view-test-token"}):
            self.app = create_app(workspace=self.directory.name,
                                  robots=(RobotProfile("r1", "test", "Robot"), RobotProfile("r2", "test", "No camera")),
                                  view_sources={"r1": self.camera})
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(self.app), base_url="http://local",
                                       headers={"X-Primitive-Token": "view-test-token"})

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.directory.cleanup()

    async def test_optional_capability_discovery_and_unknown_views(self):
        data = (await self.client.get("/api/robots/r1/views")).json()
        self.assertEqual(data["views"][0], {"id": "front", "label": "Front camera", "is_default": True})
        self.assertEqual((await self.client.get("/api/robots/r2/views")).json(), {"views": []})
        for path in ("missing/views", "r1/views/missing/stream", "r2/views/front/stream"):
            self.assertEqual((await self.client.get("/api/robots/" + path)).status_code, 404)
        self.assertEqual(self.camera.subscribed, [])

    async def test_stream_transmits_capture_metadata_without_memory_writes(self):
        records = self.app.state.console.memory.search()
        response = await self.client.get("/api/robots/r1/views/wrist/stream")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        frame = json.loads(response.text.splitlines()[1].removeprefix("data: "))
        self.assertEqual(base64.b64decode(frame["image"]), b"test-image")
        self.assertEqual(frame["captured_at"], 123.5)
        self.assertEqual(frame["metadata"], {"step": 3})
        self.assertIn("event: view_closed", response.text)
        self.assertEqual(self.camera.subscribed, ["wrist"])
        self.assertTrue(self.camera.closed)
        self.assertEqual(self.app.state.console.memory.search(), records)

    async def test_provider_failure_is_a_visible_terminal_event(self):
        self.camera.error = RuntimeError("camera disconnected")
        response = await self.client.get("/api/robots/r1/views/front/stream")
        self.assertIn("event: view_error", response.text)
        self.assertIn("camera disconnected", response.text)
        self.assertNotIn("event: view_closed", response.text)
        self.assertTrue(self.camera.closed)

    async def test_camera_endpoints_use_existing_desktop_authentication(self):
        for path in ("r1/views", "r1/views/front/stream"):
            response = await self.client.get("/api/robots/" + path, headers={"X-Primitive-Token": "wrong"})
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.camera.subscribed, [])

    async def test_disconnect_closes_provider_subscription(self):
        disconnected, closed = asyncio.Event(), asyncio.Event()

        class ContinuousCamera(TestCamera):
            async def subscribe_view(self, view_id):
                try:
                    yield ViewFrame("one", 123, "image/jpeg", b"image", 1, 1)
                    await asyncio.Event().wait()
                finally:
                    closed.set()

        app = FastAPI()
        app.include_router(view_routes({"r1"}, {"r1": ContinuousCamera()}))
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
                 "method": "GET", "path": "/api/robots/r1/views/front/stream",
                 "query_string": b"", "headers": [], "scheme": "http", "http_version": "1.1"}

        async def receive():
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and b"event: frame" in message["body"]:
                disconnected.set()

        await asyncio.wait_for(app(scope, receive, send), 1)
        self.assertTrue(closed.is_set())

    def test_catalog_contract_is_checked_once_at_registration(self):
        with self.assertRaisesRegex(ValueError, "registered robots"):
            view_routes(set(), {"r1": self.camera})
        for catalog in ((ViewSpec("front", "Front"),),
                        (ViewSpec("front", "Front", True), ViewSpec("front", "Duplicate")),
                        (ViewSpec("", "Front", True),)):
            self.camera.list_views = lambda: catalog
            with self.assertRaisesRegex(ValueError, "one default"):
                view_routes({"r1"}, {"r1": self.camera})
