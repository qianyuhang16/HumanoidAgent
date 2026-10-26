"""Guard dependency directions, independently of the current file layout inside each module."""

import ast
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[2] / "src" / "primitive_vla"
ALLOWED = {
    "agent": ("agent", "contracts", "media", "tools", "state", "verification", "providers", "config"),
    "state": ("state", "contracts", "media", "tools",),
    "execution": ("execution", "contracts", "tools"),
    "verification": ("verification", "contracts", "media", "tools", "config.schema", "providers.factory"),
    "tools": ("tools", "contracts", "media", "state.contracts", "execution.contracts"),
    "providers": ("providers", "config.schema"),
    "config": ("config", "verification.contracts"),
    "runtime": ("runtime", "agent", "state", "execution", "verification", "tools", "config", "storage"),
    "storage": ("storage",),
    "web": ("web", "config", "runtime.service", "providers.factory"),
    "contracts": (),
    "media": ("contracts",),
    "cli": ("config", "runtime.service", "web.app"),
    "__main__": ("cli",),
    "__init__": (),
}


def dependencies(path):
    parts = ["primitive_vla", *path.relative_to(SOURCE).with_suffix("").parts]
    package = parts[:-1]
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = ".".join(package[:len(package) - node.level + 1]) if node.level else ""
            module = ".".join(p for p in (base, node.module) if p)
            if node.module is None or module == "primitive_vla":
                yield from (module + "." + alias.name for alias in node.names)
            else:
                yield module


class ModuleBoundaryTests(unittest.TestCase):
    def test_dependency_directions_keep_state_execution_and_storage_independent(self):
        violations = []
        for path in SOURCE.rglob("*.py"):
            relative = path.relative_to(SOURCE)
            owner = relative.parts[0] if len(relative.parts) > 1 else path.stem
            self.assertIn(owner, ALLOWED, f"New module needs a responsibility review: {relative}")
            allowed = ALLOWED[owner]
            if relative.as_posix() == "tools/runtime.py":
                # Existing public import alias; no second dispatch implementation.
                allowed = (*allowed, "execution.runtime")
            for imported in dependencies(path):
                if not imported.startswith("primitive_vla."):
                    continue
                target = imported.removeprefix("primitive_vla.")
                if target.startswith("tools.") and owner != "tools" and target != "tools.contracts":
                    violations.append(f"{relative} -> {target} (depend on tool contracts, not adapters)")
                    continue
                if not any(target == prefix or target.startswith(prefix + ".") for prefix in allowed):
                    violations.append(f"{relative} -> {target}")
        self.assertEqual(violations, [], "\n".join(violations))

    def test_production_modules_do_not_depend_on_test_or_robot_implementations(self):
        blocked = ("tests", "unittest.mock", "primitive_vla_cobotmagic", "primitive_vla_demo",
                   "torch", "lerobot", "transformers", "sapien")
        violations = []
        for path in SOURCE.rglob("*.py"):
            for imported in dependencies(path):
                if any(imported == name or imported.startswith(name + ".") for name in blocked):
                    violations.append(f"{path.relative_to(SOURCE)} -> {imported}")
        self.assertEqual(violations, [], "\n".join(violations))
