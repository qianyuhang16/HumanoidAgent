"""Camera tool and Agent integration through the real packet framing contract."""

import asyncio
import base64
from io import BytesIO
import json
from pathlib import Path
import struct
import tempfile
from time import time_ns
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

from primitive_vla.config.loader import load_config
from primitive_vla.runtime.session import Runtime
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import PluginContext, ToolCall
from primitive_vla.tools.runtime import ToolRuntime
from primitive_vla_cobotmagic.provider import CameraTools


CONFIG = Path(__file__).resolve().parents[1] / "configs/cameras.yaml"


def snapshot():
    image = BytesIO()
    Image.new("RGB", (32, 24), (120, 20, 20)).save(image, format="JPEG")
    now = time_ns()
    return {"ok": True, "timestamp_ns": now,
        "image_timestamps_ns": {name: now - 10_000_000 for name in ("front", "left", "right")},
        "images": {name: base64.b64encode(image.getvalue()).decode() for name in ("front", "left", "right")}}


class FakeWriter:
    def __init__(self):
        self.sent = bytearray()
        self.closed = False
        self.dispatched = asyncio.Event()

    def write(self, data):
        self.sent.extend(data)
        self.dispatched.set()

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class CameraTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        self.provider = CameraTools({"host": "camera.test", "port": 48055},
            PluginContext(self.workspace, CONFIG.parent, EventLog()))
        self.runtime = ToolRuntime(self.provider.tools)
        self.addAsyncCleanup(self.provider.close)
        self.addAsyncCleanup(self.runtime.close)

    def connection(self, reply=None, *, raw=None, hanging=False):
        reader, writer = asyncio.StreamReader(), FakeWriter()
        if not hanging:
            if raw is None:
                body = json.dumps(reply if reply is not None else snapshot()).encode()
                raw = struct.pack("!I", len(body)) + body
            # Exercise the stream framing rather than replacing the client method.
            reader.feed_data(raw[:2])
            reader.feed_data(raw[2:17])
            reader.feed_data(raw[17:])
            reader.feed_eof()
        async def connect(*args, **kwargs):
            return reader, writer
        return patch("primitive_vla_cobotmagic.bridge.asyncio.open_connection", side_effect=connect), writer

    async def test_subset_returns_real_jpeg_and_never_dispatches_control(self):
        connection, writer = self.connection()
        with connection:
            result = await self.runtime.invoke(ToolCall(name="camera.observe", arguments={"cameras": ["left"]}))
        self.assertEqual(result.status, "ok", result.text)
        self.assertEqual(set(result.images), {"left"})
        with Image.open(self.workspace / "media" / result.images["left"]) as image:
            self.assertEqual(image.size, (32, 24))
        self.assertEqual(result.data["cameras"]["left"]["age_at_bridge_ms"], 10.)
        size, = struct.unpack("!I", writer.sent[:4])
        self.assertEqual(size, len(writer.sent) - 4)
        self.assertEqual(json.loads(writer.sent[4:]), {"op": "get_observation"})
        self.assertTrue(writer.closed)
        self.assertTrue(all(tool.read_only for tool in self.runtime.definitions()))

    async def test_unknown_camera_rejected_before_network(self):
        connection, writer = self.connection()
        with connection as connect:
            result = await self.runtime.invoke(ToolCall(name="camera.observe", arguments={"cameras": ["../secret"]}))
        self.assertEqual(result.status, "rejected")
        connect.assert_not_called()

    async def test_stale_missing_future_and_invalid_images_do_not_publish_files(self):
        for case in ("stale", "missing", "future", "invalid_jpeg", "invalid_base64", "not_ready"):
            with self.subTest(case=case):
                reply = snapshot()
                if case == "stale":
                    reply["image_timestamps_ns"]["left"] -= 3_000_000_000
                elif case == "missing":
                    del reply["images"]["left"]
                elif case == "future":
                    reply["image_timestamps_ns"]["left"] += 3_000_000_000
                elif case == "invalid_jpeg":
                    reply["images"]["left"] = base64.b64encode(b"not an image").decode()
                elif case == "invalid_base64":
                    reply["images"]["left"] = "%%%"
                else:
                    reply = {"ok": False, "error": "Missing streams: left_camera"}
                connection, writer = self.connection(reply)
                with connection:
                    result = await self.runtime.invoke(ToolCall(name="camera.observe"))
                self.assertEqual(result.status, "error")
                self.assertEqual(result.images, {})
                self.assertEqual(list((self.workspace / "media").iterdir()), [])
                self.assertTrue(writer.closed)

    async def test_oversized_or_truncated_response_closes_connection(self):
        for raw in (struct.pack("!I", 65 * 1024 * 1024), struct.pack("!I", 100) + b"{}"):
            connection, writer = self.connection(raw=raw)
            with connection:
                result = await self.runtime.invoke(ToolCall(name="camera.observe"))
            self.assertEqual(result.status, "error")
            self.assertTrue(writer.closed)

    async def test_timeout_closes_pending_read(self):
        self.provider.bridge.request_timeout_s = .02
        connection, writer = self.connection(hanging=True)
        with connection:
            result = await self.runtime.invoke(ToolCall(name="camera.observe"))
        self.assertEqual(result.status, "timed_out")
        self.assertTrue(writer.closed)
        self.assertEqual(self.provider.bridge.writers, set())

    async def test_cancellation_closes_pending_read(self):
        connection, writer = self.connection(hanging=True)
        with connection:
            task = asyncio.create_task(self.runtime.invoke(ToolCall(name="camera.observe")))
            await asyncio.wait_for(writer.dispatched.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(writer.closed)
        self.assertEqual(self.provider.bridge.writers, set())

    async def test_agent_receives_three_actual_image_payloads(self):
        requests = []
        def model(request):
            body = json.loads(request.content)
            requests.append(body)
            reply = {"calls": [{"name": "camera.observe", "arguments": {}}], "final": None} if len(requests) == 1 else {
                "calls": [], "final": {"status": "completed", "reason": "Images received", "evidence": []}}
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})
        config = load_config(CONFIG)
        config.model.options["require_api_key"] = False
        runtime = Runtime(config, self.workspace, EventLog(), transport=httpx.MockTransport(model), config_dir=CONFIG.parent)
        connection, writer = self.connection()
        try:
            with connection:
                report = await runtime.run("Describe the three camera views")
            self.assertEqual(report["status"], "completed")
            images = [item for item in requests[1]["messages"][1]["content"] if item["type"] == "image_url"]
            self.assertEqual(len(images), 3)
            self.assertTrue(all(item["image_url"]["url"].startswith("data:image/jpeg;base64,") for item in images))
        finally:
            await runtime.close()
