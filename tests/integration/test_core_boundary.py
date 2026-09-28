"""The Agent starts without any robot, skill catalog or installed extension."""

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from primitive_vla.config.loader import load_config, save_config
from primitive_vla.config.schema import AdapterSpec, AppConfig
from primitive_vla.runtime.session import Runtime
from primitive_vla.storage.events import EventLog


class TextModel:
    def __init__(self, options, context):
        self.context = None

    async def step(self, context, tools):
        self.context = context
        return {"final": {"status": "completed", "reason": "Task considered with supplied instructions"}}


class CoreBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_instruction_file_works_without_skill_catalog_or_robot(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "instructions.md").write_text("按用户需求检查证据。", encoding="utf-8")
            config = AppConfig(model=AdapterSpec(adapter=__name__ + ":TextModel"), instructions=["instructions.md"])
            save_config(config, root / "agent.yaml")
            loaded = load_config(root / "agent.yaml")
            self.assertEqual(loaded.tool_providers, [])
            runtime = Runtime(loaded, root / "workspace", EventLog(), config_dir=root)
            try:
                result = await runtime.run("解释可用能力")
                self.assertEqual(result["status"], "completed")
                self.assertEqual(runtime.model.context.instructions, ("按用户需求检查证据。",))
                self.assertEqual({d.name for d in runtime.tools.definitions()}, {"session.read", "session.search"})
            finally:
                await runtime.close()

    def test_scene_config_is_rejected_instead_of_silently_migrated(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scene.yaml"
            path.write_text("scene: {}\nplanner: {type: rule}\n")
            with self.assertRaises(ValidationError):
                load_config(path)

    def test_fresh_process_imports_core_without_old_domain_or_model_dependencies(self):
        script = '''
import importlib.abc
import sys
blocked = ("primitive_vla.domain", "primitive_vla.skills", "primitive_vla.simulation",
           "primitive_vla.execution", "primitive_vla.backends", "primitive_vla.vla",
           "primitive_vla_demo", "primitive_vla_cobotmagic", "primitive_vla_tabletop",
           "torch", "sapien", "lerobot", "transformers")
class BlockExternal(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("Core imported a concrete capability: " + fullname)
sys.meta_path.insert(0, BlockExternal())
from primitive_vla.runtime.service import TaskService
from primitive_vla.runtime.session import Runtime
from primitive_vla.agent.model import JSONModel
from primitive_vla.verification.runtime import Verification
from primitive_vla.web.app import create_app
from primitive_vla.config.loader import default_config
assert default_config().tool_providers == []
'''
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
