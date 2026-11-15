from importlib.util import find_spec
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.platform_support import connection, workspace

GUI_AVAILABLE = all(find_spec(name) for name in ('httpx', 'fastapi'))
if GUI_AVAILABLE:
    import httpx


@unittest.skipUnless(GUI_AVAILABLE, 'install primitive-vla[gui,vlm,test]')
class WorkbenchAssemblyTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_files_reused_across_platforms_and_profiles_without_changing_tools(self):
        from primitive_vla.application import create_workbench
        with TemporaryDirectory() as directory:
            root = Path(directory)
            platforms = root / 'workspace'
            first = workspace(platforms)
            second = platforms / 'second'
            shutil.copytree(first, second)
            data = json.loads((second / 'platform.json').read_text())
            data['id'] = 'second'
            (second / 'platform.json').write_text(json.dumps(data))
            source_files = {path: path.read_bytes() for path in platforms.rglob('*') if path.is_file()}
            configs = root / 'configs'
            configs.mkdir()
            for provider in ('deepseek', 'gpt'):
                (configs / f'{provider}.json').write_text(json.dumps({
                    'provider': provider, 'model': f'test-{provider}', 'api_key': 'test-key'}))
            model_files = {path: path.read_bytes() for path in configs.iterdir()}
            runtime_path = configs / 'runtime.json'
            for platform in ('external', 'second'):
                for profile in ('coffee', 'desk'):
                    runtime_path.write_text(json.dumps({'connection': {**connection(platforms / platform),
                        'cwd': f'../workspace/{platform}'}, 'profile': profile,
                        'run_dir': f'../data/{platform}/{profile}'}))
                    runtime_bytes = runtime_path.read_bytes()
                    run_dir = root / 'data' / platform / profile
                    for provider in ('deepseek', 'gpt'):
                        with self.subTest(provider=provider, platform=platform, profile=profile), \
                                patch.dict(os.environ, {'PRIMITIVE_DESKTOP_TOKEN': 'token'}):
                            app = create_workbench(configs / f'{provider}.json', runtime_path)
                            async with app.router.lifespan_context(app):
                                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
                                    self.assertEqual((await client.get('/api/assembly')).status_code, 401)
                                    client.headers['X-Primitive-Token'] = 'token'
                                    status = (await client.get('/api/status')).json()
                                    self.assertEqual(status['planner'], 'ModelPlanner')
                                    self.assertEqual(status['model'], f'test-{provider}')
                                    self.assertEqual(status['tools'], 3)
                                    self.assertEqual(status['skills'], 4)
                                    data = (await client.get('/api/assembly')).json()
                                    self.assertEqual(data['selected'], {'platform': platform, 'profile': profile})
                                    self.assertNotIn('vlm', data)
                                    self.assertNotIn('test-key', json.dumps(data))
                                    self.assertEqual((await client.post('/api/desktop/prepare-close')).status_code, 200)
                            self.assertTrue((run_dir / 'native/closed').exists())
                            self.assertEqual(runtime_path.read_bytes(), runtime_bytes)
            self.assertTrue(all(path.read_bytes() == content for path, content in source_files.items()))
            self.assertTrue(all(path.read_bytes() == content for path, content in model_files.items()))

    def test_invalid_config_and_unknown_selection_fail_before_opening_platform(self):
        from primitive_vla.application import create_workbench
        with TemporaryDirectory() as directory:
            root = Path(directory)
            platform = workspace(root / 'workspace')
            model_path, runtime_path = root / 'model.json', root / 'runtime.json'
            cases = [
                ({'provider': 'bad'}, {}, ValueError),
                ({'platform': 'external'}, {}, ValueError),
                ({'api_key': ['secret']}, {}, ValueError),
                ({}, {'vlm': {'api_key': 'secret'}}, ValueError),
                ({}, {'connection': {'command': []}}, ValueError),
            ]
            for model_changes, runtime_changes, error_type in cases:
                model_path.write_text(json.dumps({'provider': 'gpt', 'model': 'test', 'api_key': 'secret',
                                                 **model_changes}))
                runtime_path.write_text(json.dumps({'connection': connection(platform),
                    'profile': 'coffee', 'run_dir': 'data', **runtime_changes}))
                with self.assertRaises(error_type) as error:
                    create_workbench(model_path, runtime_path)
                self.assertNotIn('secret', str(error.exception))
                self.assertFalse((root / 'data').exists())

    async def test_environment_selects_two_independent_config_paths(self):
        from primitive_vla.application import create_workbench
        with TemporaryDirectory() as directory:
            root = Path(directory)
            platform = workspace(root)
            model_path, runtime_path = root / 'vlm.json', root / 'runtime.json'
            model_path.write_text(json.dumps({'provider': 'gpt', 'model': 'env-model', 'api_key': 'test-key'}))
            runtime_path.write_text(json.dumps({'connection': connection(platform),
                                               'profile': 'coffee', 'run_dir': 'data'}))
            with patch.dict(os.environ, {'PRIMITIVE_VLM_CONFIG': str(model_path),
                                         'PRIMITIVE_RUNTIME_CONFIG': str(runtime_path)}):
                app = create_workbench()
                async with app.router.lifespan_context(app):
                    self.assertEqual(app.state.console.agent.planner.vlm.model, 'env-model')
            self.assertTrue((root / 'data/native/closed').exists())
