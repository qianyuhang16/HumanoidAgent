"""Configuration boundaries reject unsupported execution before starting workers."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import warnings

from primitive_vla.config.loader import default_config, load_config, resolve_skills, save_config
from primitive_vla.config.schema import AppConfig
from primitive_vla.skills.loader import LIBRARY

ROOT = Path(__file__).resolve().parents[2]


class ConfigurationTests(unittest.TestCase):
    def test_legacy_fields_migrate_without_mutating_input_or_saved_output(self):
        legacy = load_config(ROOT / 'configs/libero-agent.json').model_dump(mode='json')
        legacy['scene']['mode'] = 'legacy'
        libero = legacy['backends']['libero']['libero']
        libero['objects'] = {'unused': 'mapping'}
        libero['policy']['model_repo'] = 'unused/repository'
        original = deepcopy(legacy)
        with warnings.catch_warnings(record=True) as notices:
            warnings.simplefilter('always', FutureWarning)
            migrated = AppConfig.model_validate(legacy)
        self.assertEqual(len(notices), 3)
        self.assertTrue(all(issubclass(n.category, FutureWarning) for n in notices))
        self.assertEqual(legacy, original)
        self.assertEqual(migrated.backends['libero'].libero.policy.checkpoint, libero['policy']['checkpoint'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            save_config(migrated, path)
            with warnings.catch_warnings(record=True) as notices:
                warnings.simplefilter('always', FutureWarning)
                restored = load_config(path)
            self.assertEqual(notices, [])
            self.assertEqual(restored.model_dump(), migrated.model_dump())

    def test_vla_approach_program_is_checked_for_inline_and_reloaded_skills(self):
        config = load_config(ROOT / 'configs/sapien.json')
        inline = config.model_dump(mode='json')
        move = next(s for s in inline['skills'] if s['name'] == 'MOVE_TO')
        move['program'].append({'op': 'gripper', 'opened': False})
        with self.assertRaisesRegex(ValueError, 'only approach motion'):
            AppConfig.model_validate(inline)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'SKILL.md'
            original = (LIBRARY / 'sapien_vla/move_to/SKILL.md').read_text()
            path.write_text(original.replace('kind: approach', 'kind: object_grasp'))
            config.skill_sources = [str(path)]
            with self.assertRaisesRegex(ValueError, 'only approach motion'):
                resolve_skills(config, path.parent)

    def test_local_boundary_is_rejected_at_configuration_load(self):
        config = default_config().model_dump(mode='json')
        config['skills'][0]['boundary'] = 'local'
        with self.assertRaisesRegex(ValueError, 'atomic'):
            AppConfig.model_validate(config)
