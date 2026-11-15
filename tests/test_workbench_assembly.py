from importlib.util import find_spec
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.platform_support import workspace

GUI_AVAILABLE = all(find_spec(name) for name in ('httpx', 'fastapi'))
if GUI_AVAILABLE:
    import httpx


@unittest.skipUnless(GUI_AVAILABLE, 'install primitive-vla[gui,vlm,test]')
class WorkbenchAssemblyTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_and_profile_choices_are_independent(self):
        from primitive_vla.application import create_workbench
        for provider in ('deepseek', 'gpt'):
            for profile in ('coffee', 'desk'):
                with self.subTest(provider=provider, profile=profile), TemporaryDirectory() as directory:
                    root = Path(directory)
                    workspace(root)
                    config = {'vlm': {'provider': provider, 'model': 'test-model', 'api_key': 'test-key'},
                              'platforms': '.', 'assembly': {'platform': 'external', 'profile': profile},
                              'workspace': 'run'}
                    path = root / 'config.json'
                    path.write_text(json.dumps(config))
                    with patch.dict(os.environ, {'PRIMITIVE_DESKTOP_TOKEN': 'token'}):
                        app = create_workbench(path)
                        self.assertFalse((root / 'run').exists())
                        async with app.router.lifespan_context(app):
                            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
                                self.assertEqual((await client.get('/api/assembly')).status_code, 401)
                                client.headers['X-Primitive-Token'] = 'token'
                                status = (await client.get('/api/status')).json()
                                self.assertEqual(status['planner'], 'ModelPlanner')
                                self.assertEqual(status['tools'], 3)
                                self.assertEqual(status['skills'], 4)
                                data = (await client.get('/api/assembly')).json()
                                self.assertEqual(data['selected'], {'platform': 'external', 'profile': profile})
                                self.assertEqual(data['vlm']['provider'], provider)
                                self.assertNotIn('test-key', json.dumps(data))
                                self.assertEqual((await client.post('/api/desktop/prepare-close')).status_code, 200)
                        self.assertTrue((root / 'run/native/closed').exists())

    def test_invalid_config_and_unknown_selection_fail_before_opening_platform(self):
        from primitive_vla.application import create_workbench
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace(root)
            path = root / 'config.json'
            for change, error_type in [({'vlm': {'provider': 'bad', 'model': 'x', 'api_key': 'secret'}}, ValueError),
                                       ({'assembly': {'platform': 'external', 'profile': 'missing'}}, KeyError)]:
                data = {'vlm': {'provider': 'gpt', 'model': 'test', 'api_key': 'secret'}, 'platforms': '.',
                        'assembly': {'platform': 'external', 'profile': 'coffee'}, 'workspace': 'run'}
                data.update(change)
                path.write_text(json.dumps(data))
                with self.assertRaises(error_type) as error:
                    create_workbench(path)
                self.assertNotIn('secret', str(error.exception))
                self.assertFalse((root / 'run').exists())
