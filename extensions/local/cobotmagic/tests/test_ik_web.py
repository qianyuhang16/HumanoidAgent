"""Offline click-to-plan tests. No ROS or robot motion transport."""
import asyncio
import base64
from contextlib import asynccontextmanager, redirect_stdout
from copy import deepcopy
import hashlib
from io import BytesIO, StringIO
import json
from pathlib import Path
import shlex
import tempfile
import time
import unittest
from unittest.mock import patch

import cv2
import httpx
import numpy as np
from PIL import Image
import yaml

from primitive_vla_cobotmagic.ik.execution import load_setup, reviewed_plan
from primitive_vla_cobotmagic.ik import main
from primitive_vla_cobotmagic.ik.web import create_app, ray_plane, rpy_from_matrix, compensated_target, project_path
from primitive_vla_cobotmagic.ik.kinematics import SerialIK, transform
from robot_fixture import robot_config

ROOT = Path(__file__).resolve().parents[1]
URDF = ROOT / 'assets/reference/piper_description.urdf'


def fixture(root):
    root = Path(root)
    calibration = root / 'calibration'
    calibration.mkdir()
    active = {'left': 'test', 'right': 'test', 'tcp_left': 'test', 'tcp_right': 'test'}
    (calibration / 'workspace.json').write_text(json.dumps({'active_runs': active}))
    k = [[500, 0, 320], [0, 500, 240], [0, 0, 1]]
    intrinsics = {'accepted': True, 'camera': 'front', 'distortion_model': 'opencv_pinhole',
                  'K': k, 'D': [0]*5, 'image_size': [640, 480]}
    q = [.1, 1.58, -.8, .2, .3, -.1]
    chain = SerialIK(URDF, 'base_link', 'link6', [f'joint{i}' for i in range(1, 7)], transform([0, 0, .1], [0, 0, 0]))
    point = chain.fk(q)[:3, 3] + [.003, 0, 0]
    point[2] = .17
    t = np.eye(4)
    t[:3, :3] = np.diag([1, -1, -1])
    t[:3, 3] = point + [0, 0, .5]
    configs = {}
    for arm in ('left', 'right'):
        config = robot_config()
        config.update(arm=arm, urdf=str(URDF), tcp_calibration_result=None, tcp_xyz_m=[0, 0, .1])
        config_path = root / f'{arm}.yaml'
        config_path.write_text(yaml.safe_dump(config))
        configs[arm] = str(config_path)
        for phase in ('tcp', 'handeye'):
            folder = calibration / (f'tcp/{arm}-tool' if phase == 'tcp' else f'handeye/front-to-{arm}-base') / 'runs/test'
            (folder / 'samples/one').mkdir(parents=True)
            (folder / 'samples/one/sample.json').write_text('{}')
            result = {'phase': phase, 'arm': arm, 'camera': 'front', 'accepted': True,
                      'base_frame': 'base_link', 'tip_frame': 'link6',
                      'urdf_sha256': hashlib.sha256(URDF.read_bytes()).hexdigest(),
                      'training_ids': ['one'], 'validation_ids': [],
                      'tcp_xyz_m': [0, 0, .1], 'tcp_rpy_rad': [0, 0, 0],
                      'T_base_from_camera': t.tolist()}
            (folder / 'result.json').write_text(json.dumps(result))
            (folder / 'manifest.json').write_text(json.dumps({'config': config, 'intrinsics': intrinsics}))
    config_path = root / 'web.yaml'
    config_path.write_text(yaml.safe_dump({'host': 'invalid.test', 'port': 1,
        'calibration_workspace': str(calibration), 'output_workspace': str(root / 'plans'), 'hover_configs': configs}))
    stream = BytesIO()
    Image.new('RGB', (640, 480), '#334455').save(stream, format='JPEG')
    raw = {'ok': True, 'timestamp_ns': 1_000_000_000,
           'positions': {'left': [*q, .01], 'right': [*q, .02]},
           'joint_timestamps_ns': {'left': 1_000_000_000, 'right': 1_000_000_000},
           'images': {'front': base64.b64encode(stream.getvalue()).decode()},
           'image_timestamps_ns': {'front': 1_000_000_000}}
    return config_path, raw, point


class ReadOnlyBridge:
    def __init__(self, raw):
        self.raw, self.calls = raw, 0

    async def observation(self, context):
        context.check_active()
        self.calls += 1
        return deepcopy(self.raw)

    async def close(self):
        pass


class GeometryTests(unittest.TestCase):
    def test_image_up_compensation_uses_calibration_keeps_height_and_metric_distance(self):
        intrinsics = {'accepted': True, 'camera': 'front', 'distortion_model': 'opencv_pinhole',
                      'K': [[500, 0, 320], [0, 500, 240], [0, 0, 1]],
                      'D': [.1, -.02, .003, .002, 0], 'image_size': [640, 480]}
        for yaw in (0., .7, -1.2):
            camera = transform([.2, -.1, .9], [np.pi, .15, yaw])
            for pixel in ([320,240], [300,0]):
                for distance in (.10, .15):
                    point, target = compensated_target(pixel, [640,480], intrinsics, camera, .17, distance)
                    self.assertAlmostEqual(target[2], .17)
                    self.assertAlmostEqual(np.linalg.norm(target-point), distance)
                    projected = project_path([target], np.linalg.inv(camera), intrinsics)[0]
                    self.assertLess(projected[1], pixel[1])

    def test_distortion_aware_plane_intersection_and_invalid_geometry(self):
        intrinsics = {'accepted': True, 'camera': 'front', 'distortion_model': 'opencv_pinhole',
                      'K': [[500, 0, 320], [0, 500, 240], [0, 0, 1]],
                      'D': [.1, -.02, .003, .002, 0], 'image_size': [640, 480]}
        p = np.array([.1, -.1, 1.0])
        uv, _ = cv2.projectPoints(p.reshape(1, 3), np.zeros(3), np.zeros(3),
                                 np.array(intrinsics['K'], float), np.array(intrinsics['D']))
        np.testing.assert_allclose(ray_plane(uv.reshape(2), [640, 480], intrinsics, np.eye(4), 1), p, atol=1e-7)
        for pixel, size, z in [([700, 240], [640,480], 1), ([320,240], [320,240], 1), ([320,240], [640,480], -1)]:
            with self.assertRaises(ValueError):
                ray_plane(pixel, size, intrinsics, np.eye(4), z)
        parallel = transform([0,0,0], [0, np.pi/2, 0])
        with self.assertRaises(ValueError):
            ray_plane([320,240], [640,480], intrinsics, parallel, 1)

    def test_orientation_roundtrip_including_singular_pitch(self):
        for angles in [[.1, .8, -.2], [.2, np.pi/2, .3], [.2, -np.pi/2, .3]]:
            r = transform([0,0,0], angles)[:3,:3]
            np.testing.assert_allclose(transform([0,0,0], rpy_from_matrix(r))[:3,:3], r, atol=1e-8)


class WebTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def site(self):
        with tempfile.TemporaryDirectory(prefix='ik space ') as tmp:
            config, raw, point = fixture(tmp)
            bridge = ReadOnlyBridge(raw)
            app = create_app(config, bridge=bridge)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                    yield app, client, bridge, point

    async def select(self, client, point):
        reply = await client.post('/api/capture')
        self.assertEqual(reply.status_code, 200, reply.text)
        return {'observation_id': reply.json()['observation_id'], 'u': 320, 'v': 240}

    async def test_click_exports_reproducible_plan_and_cli_without_motion(self):
        async with self.site() as (app, client, bridge, point):
            with patch('socket.create_connection') as connect:
                body = await self.select(client, point)
                body['move_duration_s'] = 10
                reply = await client.post('/api/calculate', json=body)
                self.assertEqual(reply.status_code, 200, reply.text)
                data = reply.json()
                self.assertTrue(data['execution_ready'], data)
                self.assertAlmostEqual(data['duration_s'], 3.)
                self.assertEqual(data['orientation_mode'], 'downward')
                self.assertTrue(data['jaw_heading_preserved'])
                delta = np.deg2rad(data['target_jaw_heading_deg'] - data['start_jaw_heading_deg'])
                self.assertLess(abs(np.arctan2(np.sin(delta), np.cos(delta))), np.deg2rad(2))
                self.assertGreaterEqual(data['target_downward_angle_deg'], 40)
                self.assertLessEqual(data['target_downward_angle_deg'], 90)
                np.testing.assert_allclose(data['point_xyz_m'], point)
                np.testing.assert_allclose(data['target_xyz_m'], point + [0,.15,0])
                self.assertEqual(data['compensation']['distance_m'], .15)
                self.assertLess(data['target_pixel'][1], body['v'])
                command = shlex.split(data['command'])
                self.assertIn('--execute', command)
                self.assertIn('--plan', command)
                self.assertEqual(bridge.calls, 1)  # Calculation cannot send anything to ROS.
                args = shlex.split(data['preview_command'])[3:]
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(args), 0)
                connect.assert_not_called()
                folder = Path(data['bundle'])
                config, chain = load_setup(folder / 'hover.yaml')
                self.assertEqual(config['target_tcp_xyz_m'][2], .17)
                np.testing.assert_allclose(config['target_tcp_xyz_m'], data['target_xyz_m'])
                self.assertNotIn('bowl_rim_center_xyz_m', config)
                self.assertNotIn('hover_height_m', config)
                saved = json.loads((folder / 'plan.json').read_text())
                moved = deepcopy(bridge.raw['positions']); moved['right'][0] += .02
                with self.assertRaisesRegex(ValueError, 'moved'):
                    reviewed_plan(config, chain, saved, moved)
                saved['joint_path_rad'][-1][0] += .01
                with self.assertRaisesRegex(ValueError, 'changed'):
                    reviewed_plan(config, chain, saved, bridge.raw['positions'])
                for endpoint in ('/api/execute', '/api/action', '/api/enable'):
                    self.assertEqual((await client.post(endpoint)).status_code, 404)

    async def test_existing_bridge_negative_gripper_exports_execute_command(self):
        async with self.site() as (_, client, bridge, point):
            bridge.raw['positions']['right'][6] = -.0043
            body = await self.select(client, point)
            data = (await client.post('/api/calculate', json=body)).json()
            self.assertTrue(data['solved'], data)
            self.assertTrue(data['execution_ready'], data)
            self.assertIn('--execute', data['command'])
            self.assertEqual(data['bridge_mode'], 'existing_send_action')
            self.assertEqual(data['gripper_hold_commands_m']['right'], 0.0)
            self.assertEqual(bridge.raw['positions']['right'][6], -.0043)

    async def test_left_arm_applies_ten_centimetres_once(self):
        async with self.site() as (_, client, bridge, point):
            body = await self.select(client, point)
            body['arm'] = 'left'
            data = (await client.post('/api/calculate', json=body)).json()
            self.assertTrue(data['execution_ready'], data)
            np.testing.assert_allclose(data['target_xyz_m'], point + [0,.10,0])
            self.assertEqual(data['compensation']['distance_m'], .10)
            folder = Path(data['bundle'])
            inputs = json.loads((folder/'inputs.json').read_text())
            np.testing.assert_allclose(inputs['uncompensated_point_xyz_m'], point)
            config, chain = load_setup(folder/'hover.yaml')
            saved = json.loads((folder/'plan.json').read_text())
            replayed = reviewed_plan(config, chain, saved, bridge.raw['positions'])
            np.testing.assert_allclose(replayed['target_tcp_xyz_m'], point + [0,.10,0])

    async def test_new_failed_or_expired_capture_rejects_old_click_and_cross_origin(self):
        async with self.site() as (app, client, bridge, point):
            old = await self.select(client, point)
            await self.select(client, point)
            self.assertEqual((await client.post('/api/calculate', json=old)).status_code, 409)
            body = await self.select(client, point)
            app.state.workspace.snapshot['captured'] = time.monotonic() - 181
            self.assertEqual((await client.post('/api/calculate', json=body)).status_code, 409)
            body = await self.select(client, point)
            bridge.raw['image_timestamps_ns']['front'] -= 800_000_000
            self.assertEqual((await client.post('/api/capture')).status_code, 409)
            self.assertIsNone(app.state.workspace.snapshot)
            self.assertEqual((await client.post('/api/calculate', json=body)).status_code, 409)
            self.assertEqual((await client.post('/api/capture', headers={'origin':'http://evil'})).status_code, 403)
            body['execute'] = True
            self.assertEqual((await client.post('/api/calculate', json=body)).status_code, 422)

    async def test_left_arm_uses_own_base_fixed_height_and_validates_calibration(self):
        async with self.site() as (app, client, bridge, point):
            w = app.state.workspace
            folder, result, _ = w.calibration_result('left', 'handeye')
            result['T_base_from_camera'][1][3] += .6
            (folder/'result.json').write_text(json.dumps(result))
            body = await self.select(client, point)
            body['arm'] = 'left'
            data = (await client.post('/api/calculate', json=body)).json()
            np.testing.assert_allclose(data['point_xyz_m'], point + [0,.6,0])
            self.assertFalse(data['solved'])  # This synthetic distant target is unreachable.
            self.assertIsNone(data['command'])
            result['accepted'] = False
            (folder/'result.json').write_text(json.dumps(result))
            self.assertEqual((await client.post('/api/calculate', json=body)).status_code, 409)


if __name__ == '__main__':
    unittest.main()
