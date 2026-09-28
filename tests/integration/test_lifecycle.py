"""Runtime lifecycle works through plugin interfaces and cleans up on errors."""

import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from primitive_vla.config.loader import default_config
from primitive_vla.config.schema import AdapterSpec
from primitive_vla.runtime.lifecycle import close_all
from primitive_vla.runtime.session import Runtime
from primitive_vla.storage.events import EventLog


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_owns_model_and_tool_provider_lifecycles(self):
        config = default_config()
        config.model = AdapterSpec(adapter="tests.integration.test_tool_runtime:TestModel",
                                   options={"tool": "camera.observe"})
        config.tool_providers = [AdapterSpec(adapter="tests.integration.test_tool_runtime:CameraTools")]
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(config, Path(directory), EventLog())
            try:
                result = await runtime.run("Inspect the available camera")
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(result['results'][0]['tool'], 'camera.observe')
            finally:
                await runtime.close()
            self.assertTrue(runtime.model.closed)
            self.assertTrue(runtime.resources[1].closed)

    async def test_cleanup_finishes_all_resources_after_error_or_cancellation(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
                async def close_slow():
                    entered.set()
                    await release.wait()
                    finished.set()
                bad = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError('close failed')))
                slow = SimpleNamespace(close=close_slow)
                closing = asyncio.create_task(close_all(bad, slow))
                await entered.wait()
                if cancel:
                    closing.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError if cancel else RuntimeError):
                    await closing
                self.assertTrue(finished.is_set())
                bad.close.assert_awaited_once()
