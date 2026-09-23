import unittest
from pathlib import Path

from primitive_vla.skills.loader import load_catalog


class SkillCatalogTests(unittest.TestCase):
    def test_builtin_catalog_parses_execution_declarations(self):
        catalog = load_catalog(["builtin:kitchen"], Path.cwd())
        self.assertTrue(catalog.specs)
        self.assertTrue(all(spec.execution["type"] in {"program", "policy"} for spec in catalog.specs.values()))
