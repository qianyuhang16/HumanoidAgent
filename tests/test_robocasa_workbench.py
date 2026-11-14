import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch
from importlib.util import find_spec

GUI_AVAILABLE = all(find_spec(name) for name in ("httpx", "fastapi"))
if GUI_AVAILABLE:
    import httpx
from tests.support import Provider, spec


@unittest.skipUnless(GUI_AVAILABLE, "install primitive-vla[gui,deepseek] for desktop composition")
class DeepSeekWorkbenchTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_registers_environment_and_closes_it_after_runtime(self):
        from primitive_vla.interfaces.web.deepseek import create_workbench

        class Environment:
            closed = False

            @property
            def views(self):
                return self

            def list_views(self):
                return ()

            def register(self, registry, resources, skills):
                registry.register(spec("state.attached"), Provider())
                resources.define_robot("robocasa-1", {"simulation"})

            async def close(self):
                self.closed = True

        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps({"api_key": "test-key", "workspace": directory}))
            environment = Environment()
            with patch.dict(os.environ, {"PRIMITIVE_DEEPSEEK_CONFIG": str(config_path)}), patch(
                "primitive_vla.interfaces.web.deepseek.RoboCasa.open", AsyncMock(return_value=environment)
            ):
                app = create_workbench()
                async with app.router.lifespan_context(app):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as client:
                        status = (await client.get("/api/status")).json()
                        self.assertTrue(status["connected"])
                        self.assertEqual(status["planner"], "ModelPlanner")
                        self.assertEqual(status["tools"], 1)
                        self.assertIn("仿真", status["robots"][0]["label"])
                        self.assertNotIn("test-key", json.dumps(status))
                        self.assertEqual((await client.get("/api/robots/robocasa-1/views")).json(), {"views": []})
                        self.assertEqual((await client.post("/api/desktop/prepare-close")).status_code, 200)
                self.assertTrue(environment.closed)
