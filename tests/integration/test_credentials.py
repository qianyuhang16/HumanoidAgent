"""Credential checks are framework behavior, independent of any robot adapter."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from primitive_vla.config.loader import save_config
from primitive_vla.config.schema import AdapterSpec, AppConfig
from primitive_vla.providers.base import ProviderError
from primitive_vla.runtime.session import Runtime
from primitive_vla.storage.events import EventLog
from primitive_vla.web.app import create_app


def required_key_config():
    return AppConfig(model=AdapterSpec(adapter="primitive_vla.agent.model:JSONModel", options={
        "model": "test-model", "api_key_env": "TEST_VLM_API_KEY", "require_api_key": True,
    }), tool_providers=[])


class CredentialTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_key_fails_before_loading_tool_providers(self):
        config = required_key_config()
        # This provider cannot be imported. Credential validation must run first.
        config.tool_providers = [AdapterSpec(adapter="unavailable_robot:Factory")]
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"TEST_VLM_API_KEY": ""}):
            runtime = Runtime(config, Path(tmp), EventLog())
            with self.assertRaisesRegex(ProviderError, "TEST_VLM_API_KEY"):
                await runtime.start()
            await runtime.close()

    async def test_missing_key_has_actionable_web_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"TEST_VLM_API_KEY": ""}):
            config_path = Path(tmp) / "config.yaml"
            save_config(required_key_config(), config_path)
            app = create_app(Path(tmp), config_path)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.post("/api/vlm/test")
                    self.assertEqual(response.status_code, 502)
                    self.assertIn("TEST_VLM_API_KEY", response.json()["detail"])
