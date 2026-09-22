"""Composition root keeps adapters selectable without changing Agent code."""

import tempfile
import unittest
from pathlib import Path

from primitive_vla.config.loader import default_config
from primitive_vla.runtime.composition import load_adapter
from primitive_vla.runtime.generic_service import GenericService


class CompositionTests(unittest.TestCase):
    def test_default_runtime_adapters_are_loaded_from_yaml(self):
        config = default_config()
        self.assertEqual(config.adapters.vlm, 'primitive_vla.providers.factory:create_client')
        self.assertEqual(config.adapters.agent, 'primitive_vla.agent.loop:Agent')
        self.assertIs(load_adapter(config.adapters.agent), load_adapter('primitive_vla.agent.loop:Agent'))

    def test_scene_and_vla_documents_select_independent_adapters(self):
        with tempfile.TemporaryDirectory() as workspace:
            service = GenericService(Path.cwd(), Path(workspace))
            battery = service.config('battery')
            lab = service.external_config('vla-lab.yaml')
        self.assertEqual(battery['adapters']['scene'], 'primitive_vla.backends.rmbench:RMBenchBackend')
        self.assertEqual(battery['policy_adapter'], 'primitive_vla.vla.mem0:Mem0Policy')
        self.assertEqual(lab['policy_adapter'], 'primitive_vla.vla.rmbench_pi05_client:RMBenchPi05Client')
        self.assertEqual(lab['adapters']['scene'], battery['adapters']['scene'])
        self.assertNotEqual(lab['policy_adapter'], battery['policy_adapter'])
