"""Web camera/chat behavior with actual image files and a mocked VLM transport."""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from PIL import Image

from primitive_vla.tools import ToolResult
from primitive_vla_cobotmagic.web import create_app

CONFIG = Path(__file__).resolve().parents[1] / "configs/cameras.yaml"


class WebTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def site(self, model):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
            app = create_app(CONFIG, Path(tmp), transport=httpx.MockTransport(model))
            async with app.router.lifespan_context(app):
                chat = app.state.chat
                async def observe(arguments, context):
                    token = uuid4().hex
                    images = {}
                    for name in arguments["cameras"]:
                        ref = f"{token}-{name}.jpg"
                        Image.new("RGB", (32,24)).save(chat.media / ref)
                        images[name] = ref
                    return ToolResult(images=images, data={"observation_id": token})
                chat.camera.observe = observe
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    yield app, client

    async def test_preview_is_ephemeral_and_chat_preserves_its_own_evidence(self):
        payloads = []
        def model(request):
            body = json.loads(request.content)
            payloads.append(body)
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"answer":"一个碗。"}'}}]})
        async with self.site(model) as (app, client):
            chat = app.state.chat
            preview = await client.post("/api/preview", json={"cameras": ["front"]})
            self.assertTrue(preview.json()["images"]["front"].startswith("data:image/jpeg;base64,"))
            self.assertEqual(list(chat.media.iterdir()), [])
            for question in ("这里有什么？", "它在哪里？"):
                response = await client.post("/api/chat", json={"question": question, "cameras": ["left"]})
                self.assertEqual(response.status_code, 202)
                await chat.task
                self.assertEqual(chat.turns[-1]["status"], "completed")
            context = json.loads(payloads[1]["messages"][1]["content"][0]["text"])
            self.assertEqual(context["previous_turns"][0]["question"], "这里有什么？")
            images = [x for x in payloads[1]["messages"][1]["content"] if x["type"] == "image_url"]
            self.assertEqual(len(images), 1)
            self.assertTrue(images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
            first = chat.turns[0]["images"]["left"]
            second = chat.turns[1]["images"]["left"]
            self.assertNotEqual(first, second)
            self.assertEqual((await client.get('/api/media/'+first)).status_code, 200)
            await client.post("/api/preview", json={})
            self.assertEqual(len(list(chat.media.iterdir())), 2)
            self.assertNotIn("test-key", chat.path.read_text())
            state = (await client.get('/api/state')).json()
            self.assertTrue(state["key_configured"])
            self.assertNotIn("test-key", json.dumps(state))

    async def test_cancel_and_busy_prevent_overlapping_answers(self):
        entered = asyncio.Event()
        async def model(request):
            entered.set()
            await asyncio.Event().wait()
        async with self.site(model) as (app, client):
            await client.post('/api/chat', json={"question": "看到什么？"})
            await asyncio.wait_for(entered.wait(), 1)
            self.assertEqual((await client.post('/api/chat', json={"question": "另一问"})).status_code,409)
            self.assertEqual((await client.post('/api/clear', json={})).status_code,409)
            self.assertEqual((await client.post('/api/cancel', json={})).status_code,200)
            self.assertEqual(app.state.chat.turns[-1]["status"], "cancelled")
            self.assertFalse(app.state.chat.busy)

    async def test_missing_key_and_camera_errors_do_not_call_model(self):
        requests = []
        def model(request):
            requests.append(request)
            raise AssertionError("Model must not be called")
        async with self.site(model) as (app, client):
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
                await client.post('/api/chat',json={"question":"看看桌面"})
                await app.state.chat.task
            self.assertEqual(app.state.chat.turns[-1]["status"], "failed")
            async def broken(*args):
                raise ValueError("stale frame")
            app.state.chat.camera.observe = broken
            await client.post('/api/chat',json={"question":"再看看"})
            await app.state.chat.task
            self.assertEqual(app.state.chat.turns[-1]["error"], "stale frame")
            self.assertEqual(requests, [])

    async def test_input_origin_and_static_routes(self):
        async with self.site(lambda request: None) as (app, client):
            self.assertEqual((await client.get('/')).status_code,200)
            self.assertEqual((await client.get('/static/app.js')).status_code,200)
            self.assertEqual((await client.post('/api/chat',json={"question":" "})).status_code,422)
            self.assertEqual((await client.post('/api/preview',json={"cameras":["front","front"]})).status_code,422)
            self.assertEqual((await client.post('/api/chat',headers={"Origin":"http://other"},json={"question":"test"})).status_code,403)
            self.assertEqual((await client.get('/api/media/conversation.json')).status_code,404)
