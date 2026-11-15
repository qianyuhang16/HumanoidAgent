import ast
from pathlib import Path
import unittest


class ModuleBoundaryTests(unittest.TestCase):
    def test_platform_and_skill_assembly_do_not_choose_a_vlm(self):
        root = Path(__file__).parents[1] / "src" / "primitive_vla"
        assembly = root / "tools" / "assembly.py"
        connections = root / "runtime" / "connections"
        paths = [assembly, *connections.glob("*.py")]
        for path in paths:
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                self.assertFalse(any(part in name.split(".") for name in names
                                     for part in ("vlm", "planning", "application", "interfaces")), path)
                if path == assembly:
                    self.assertFalse(any(part in name.split(".") for name in names
                                         for part in ("runtime", "subprocess", "asyncio")), path)
                else:
                    self.assertFalse(any(name.startswith('primitive_vla.tools.assembly') for name in names), path)

    def test_core_has_no_builtin_platform_or_sdk_imports(self):
        root = Path(__file__).parents[1] / "src" / "primitive_vla"
        self.assertFalse((root / "providers").exists())
        for path in root.rglob("*.py"):
            self.assertNotIn("robocasa", path.read_text())

    def test_vlm_adapters_do_not_depend_on_agent_policy_or_memory(self):
        root = Path(__file__).parents[1] / "src" / "primitive_vla"
        for path in (root / "vlm").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                for module in modules:
                    self.assertFalse(any(part in module.split(".") for part in
                                         ("planning", "memory", "runtime", "providers", "tools", "skills", "application")), path)

    def test_planning_has_no_provider_http_client(self):
        root = Path(__file__).parents[1] / "src" / "primitive_vla" / "planning"
        for path in root.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                self.assertFalse(any(name.split(".")[0] in {"httpx", "requests", "openai"} for name in names), path)
