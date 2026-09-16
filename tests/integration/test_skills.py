"""File-defined skills, hot reload and immutable task snapshots."""

from pathlib import Path
import shutil
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from primitive_vla.agent.task_parser import DEFAULT_INSTRUCTION
from primitive_vla.config.loader import default_config, save_config
from primitive_vla.skills.loader import LIBRARY, load_skills
from primitive_vla.web.app import create_app


class MarkdownSkillTests(unittest.TestCase):
    def test_new_skill_name_and_reload_take_effect_without_python_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / 'skills'
            shutil.copytree(LIBRARY / 'kitchen', library)
            skill = library / 'grasp/SKILL.md'
            skill.write_text(skill.read_text().replace('name: GRASP', 'name: PICK_ITEM') + '\nOriginal skill instruction.\n')
            config = default_config()
            config.skill_sources = ['skills']
            config.backends['mock'].delay_s = .02
            save_config(config, root / 'config.json')
            with TestClient(create_app(root)) as client:
                skill_names = [s['name'] for s in client.get('/api/config').json()['config']['skills']]
                self.assertIn('PICK_ITEM', skill_names)
                self.assertNotIn('GRASP', skill_names)
                task_id = client.post('/api/tasks', json={'instruction': DEFAULT_INSTRUCTION}).json()['id']
                skill.write_text(skill.read_text().replace('Original skill instruction.', 'Updated skill instruction.'))
                loaded = client.post('/api/skills/reload').json()['config']['skills']
                self.assertIn('Updated skill instruction.', next(s for s in loaded if s['name'] == 'PICK_ITEM')['instructions'])
                for _ in range(300):
                    task = client.get('/api/tasks/' + task_id).json()
                    if task['status'] not in ('running', 'cancelling'):
                        break
                    time.sleep(.01)
                self.assertEqual(task['status'], 'completed')
                self.assertIn('PICK_ITEM', [r['skill'] for r in task['report']['results']])
                snapshot = next(s for s in task['config']['skills'] if s['name'] == 'PICK_ITEM')
                self.assertIn('Original skill instruction.', snapshot['instructions'])
                self.assertNotIn('Updated skill instruction.', snapshot['instructions'])
            self.assertNotIn('"skills":', (root / 'config.json').read_text())

    def test_duplicate_and_unsafe_yaml_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate skill'):
            load_skills(['builtin:kitchen', 'builtin:kitchen'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'SKILL.md'
            path.write_text('---\n!!python/object/apply:os.system ["echo unexpected"]\n---\n')
            with self.assertRaisesRegex(ValueError, 'Invalid skill'):
                load_skills([str(path)])
