import ast
from pathlib import Path
import unittest


class ModuleBoundaryTests(unittest.TestCase):
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
                                         ("planning", "memory", "runtime", "providers")), path)

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
