"""Offline workspace, session isolation and browser API regression checks."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from primitive_vla_cobotmagic.calibration.core import DEFAULT_CONFIG, Dataset, read_config, write_json
from primitive_vla_cobotmagic.calibration.workspace import CalibrationWorkspace, read_result
from primitive_vla_cobotmagic.calibration.web import create_workspace_app


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = read_config(DEFAULT_CONFIG)
        self.intrinsics = {'accepted': True, 'phase': 'intrinsics', 'camera': 'front',
            'distortion_model': 'opencv_pinhole', 'K': [[900,0,640],[0,900,360],[0,0,1]],
            'D': [0.]*5, 'image_size': [1280,720], 'training_ids': [], 'validation_ids': []}
        self.w = CalibrationWorkspace(self.root, DEFAULT_CONFIG)
        self.addCleanup(lambda: asyncio.run(self.w.close()))

    def legacy(self):
        d = Dataset(self.root/'intrinsics', self.config, 'intrinsics')
        write_json(d.folder/'result.json', self.intrinsics)
        left = deepcopy(self.config); left['arm'] = 'left'
        l = Dataset(self.root/'handeye-left', left, 'handeye', self.intrinsics)
        write_json(l.folder/'result.json', {'accepted':False, 'error':'example failed result'})
        self.w.import_legacy()
        return d, l

    def test_shared_config_keeps_arm_settings_independent(self):
        left, right = self.w.configs['left'], self.w.configs['right']
        self.assertEqual(left['arm'], 'left')
        self.assertEqual(right['arm'], 'right')
        self.assertEqual({k: v for k, v in left.items() if k != 'arm'},
                         {k: v for k, v in right.items() if k != 'arm'})
        left['board']['square_m'] = .03
        left['joint_names'][0] = 'changed'
        self.assertEqual(right['board']['square_m'], .0272)
        self.assertEqual(right['joint_names'][0], 'joint1')

    def test_existing_both_arm_sessions_resume_with_shared_config(self):
        for arm in ('left', 'right'):
            folder = self.w.folder(arm, 'old')
            old = Dataset(folder, read_config(DEFAULT_CONFIG, arm=arm), 'handeye', self.intrinsics)
            before = old.manifest_path.read_bytes()
            _, resumed = self.w.prepare(arm, 'old', new=False)
            self.assertEqual(resumed.config['arm'], arm)
            self.assertEqual(old.manifest_path.read_bytes(), before)

    def test_import_preserves_originals_is_idempotent_and_names_phases(self):
        d, l = self.legacy()
        original = (d.folder/'manifest.json').read_bytes()
        index = deepcopy(self.w.index)
        self.w.import_legacy()
        self.assertEqual(self.w.index, index)
        self.assertEqual((d.folder/'manifest.json').read_bytes(), original)
        self.assertEqual(self.w.state()['steps'][0]['runs'][0]['result']['accepted'], True)
        self.assertEqual(self.w.state()['steps'][1]['runs'][0]['result']['accepted'], False)
        self.assertEqual(self.w.state()['steps'][2]['runs'], [])
        self.assertIn('camera-intrinsics/front', self.w.index['imports']['intrinsics'])
        self.assertIn('handeye/front-to-left-base', self.w.index['imports']['handeye-left'])

    def test_arm_switch_resets_capture_and_preserves_intrinsic_snapshot(self):
        self.legacy()
        async def run():
            await self.w.select('left', new=True)
            old = self.w.collector
            old.latest = {'sentinel': 'left frame'}
            old.bridge.close = AsyncMock()
            revision = self.w.revision
            folder = old.dataset.folder
            await self.w.select('right', new=True)
            old.bridge.close.assert_awaited_once()
            self.assertIsNone(self.w.collector.latest)
            self.assertEqual(self.w.collector.config['arm'], 'right')
            self.assertEqual(self.w.collector.dataset.intrinsics, self.intrinsics)
            self.assertNotEqual(folder, self.w.collector.dataset.folder)
            with self.assertRaisesRegex(ValueError, '切换'):
                self.w.check_revision(revision)
            self.assertEqual(json.loads((folder/'manifest.json').read_text())['config']['arm'], 'left')
        asyncio.run(run())

    def test_intrinsics_required_and_new_samples_make_old_result_stale(self):
        async def run():
            with self.assertRaisesRegex(ValueError, '内参'):
                await self.w.select('left', new=True)
            self.legacy()
            folder = self.w.folder('intrinsics', self.w.index['active_runs']['intrinsics'])
            write_json(folder/'samples'/'new-sample'/'sample.json', {'id': 'new-sample'})
            self.assertTrue(read_result(folder)['stale'])
            with self.assertRaisesRegex(ValueError, '内参'):
                await self.w.select('right', new=True)
        asyncio.run(run())

    def test_session_and_workspace_locks(self):
        self.w.acquire()
        other = CalibrationWorkspace(self.root, DEFAULT_CONFIG)
        with self.assertRaisesRegex(ValueError, '工作区'):
            other.acquire()
        async def run():
            await self.w.select('intrinsics', new=True)
            other.index = deepcopy(self.w.index)
            try:
                with self.assertRaisesRegex(ValueError, '采集服务'):
                    await other.select('intrinsics')
            finally:
                await other.close()
        asyncio.run(run())

    def test_changed_acceptance_limits_resume_without_rewriting_manifest(self):
        old_config = deepcopy(self.config)
        old_config['quality'].update(max_validation_translation_m=.01, max_validation_rotation_deg=3.)
        original = Dataset(self.root/'old', old_config, 'handeye', self.intrinsics)
        before = original.manifest_path.read_bytes()
        resumed = Dataset(original.folder, self.config, 'handeye', self.intrinsics)
        self.assertEqual(resumed.config['quality']['max_validation_translation_m'], .02)
        self.assertEqual(resumed.config['quality']['max_validation_rotation_deg'], 5.)
        self.assertEqual(original.manifest_path.read_bytes(), before)
        write_json(original.folder/'result.json', {'phase':'handeye', 'accepted':True,
            'thresholds':old_config['quality'], 'training_ids':[], 'validation_ids':[]})
        result = read_result(original.folder, {'max_validation_translation_m':.02, 'max_validation_rotation_deg':5.})
        self.assertTrue(result['thresholds_changed'])
        self.assertTrue(result['stale'])

    def test_web_switch_record_solve_revision_and_origin(self):
        self.legacy()
        async def run():
            # ASGITransport does not start lifespan or ROS polling.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_workspace_app(self.w)), base_url='http://test') as client:
                state = (await client.get('/api/state')).json()
                revision = state['revision']
                r = await client.post('/api/select', json={'revision':revision,'step':'left','new':True})
                self.assertEqual(r.status_code,200,r.text)
                revision = r.json()['revision']
                self.w.collector.record = lambda split: 'left-'+split
                r = await client.post('/api/record', json={'revision':revision,'split':'train'})
                self.assertEqual(r.json()['id'],'left-train')
                with patch('primitive_vla_cobotmagic.calibration.workspace.solve_session', return_value={'accepted':False}) as solver:
                    self.assertEqual((await client.post('/api/solve', json={'revision':revision})).json(), {'accepted':False})
                    solver.assert_called_once_with(self.w.collector.dataset.folder, acceptance_limits={'max_validation_translation_m': .02, 'max_validation_rotation_deg': 5.})
                r = await client.post('/api/select', json={'revision':revision,'step':'right','new':True})
                self.assertEqual(r.status_code,200,r.text)
                r = await client.post('/api/record', json={'revision':revision,'split':'train'})
                self.assertEqual(r.status_code,409)
                r = await client.post('/api/solve', json={'revision':self.w.revision}, headers={'Origin':'https://other.test'})
                self.assertEqual(r.status_code,403)
                r = await client.post('/api/select', json={'revision':self.w.revision,'step':'right','run':'../../elsewhere'})
                self.assertEqual(r.status_code,409)
        asyncio.run(run())

    def test_failed_solve_is_visible_without_ros(self):
        async def run():
            await self.w.select('intrinsics', new=True)
            with self.assertRaisesRegex(ValueError,'training'):
                await self.w.solve()
            self.assertFalse(self.w.state()['result']['accepted'])
            self.assertIn('training', self.w.state()['result']['error'])
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
