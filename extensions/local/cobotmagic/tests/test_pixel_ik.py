"""Public pixel entrypoint tests; cameras and motion transport are always fake."""
import asyncio
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import yaml

from primitive_vla_cobotmagic.ik import main, move_to_pixel
from primitive_vla_cobotmagic.ik.config import load_pixel_settings, MotionParameters
from primitive_vla_cobotmagic.ik.pixel import PixelIK, PixelTarget, execute_bundle
from primitive_vla_cobotmagic.ik.execution import load_setup, reviewed_plan
from test_ik_web import fixture, ReadOnlyBridge, URDF
from test_hover import FakeBridge, Clock


def standalone_config(path):
    settings = yaml.safe_load(path.read_text())
    settings.pop('hover_configs')
    settings.update(robot={'urdf': str(URDF)}, target_base_z_m=.19,
                    forward_compensation_m={'left': .03, 'right': .04},
                    motion={'move_duration_s': 5., 'downward_angle_range_deg': [45, 65],
                            'preserve_jaw_heading': False, 'speed_percent': 25,
                            'settle_timeout_s': 2., 'position_tolerance_m': .006})
    path.write_text(yaml.safe_dump(settings))
    return settings


class PixelEntryTests(unittest.TestCase):
    def test_boundary_feedback_passes_pixel_precheck_and_reports_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, raw, _ = fixture(tmp)
            standalone_config(path)
            raw['positions']['left'][1:3] = [-.002, .0065]
            camera = ReadOnlyBridge(raw)
            planner = PixelIK(path, bridge=camera)
            result = asyncio.run(planner.plan('left', 320, 240))
            self.assertTrue(result['execution_ready'], result)
            self.assertIn('start_joint_limit_projection_deg', result)
            folder = Path(result['bundle'])
            config, chain = load_setup(folder/'hover.yaml')
            self.assertEqual(config['feedback_limit_tolerance_deg'], .5)
            saved = json.loads((folder/'plan.json').read_text())
            self.assertEqual(saved['start_positions'], raw['positions'])
            self.assertEqual(reviewed_plan(config, chain, saved, raw['positions']), saved)

    def test_bad_feedback_is_reported_as_start_error_not_target_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, raw, _ = fixture(tmp)
            standalone_config(path)
            raw['positions']['left'][2] = .04
            planner = PixelIK(path, bridge=ReadOnlyBridge(raw))
            result = asyncio.run(planner.plan('left', 320, 240))
            self.assertFalse(result['execution_ready'])
            message = ' '.join(result['blockers'])
            self.assertIn('joint3', message)
            self.assertIn('尚未进行目标 IK 求解', message)
            self.assertNotIn('请换标点', message)

    def test_invalid_pixel_or_arm_does_not_read_camera(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, raw, _ = fixture(tmp)
            camera = ReadOnlyBridge(raw)
            planner = PixelIK(path, bridge=camera)
            for arm, u, v in [('middle', 320, 240), ('right', -1, 240),
                              ('left', float('nan'), 240)]:
                with self.subTest(arm=arm, u=u), self.assertRaises(ValueError):
                    asyncio.run(planner.plan(arm, u, v))
            self.assertEqual(camera.calls, 0)

    def test_one_entry_uses_standalone_config_and_freezes_effective_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, raw, point = fixture(tmp)
            standalone_config(path)
            camera = ReadOnlyBridge(raw)
            with patch('primitive_vla_cobotmagic.ik.pixel.CameraBridge', return_value=camera), \
                 patch('socket.create_connection') as socket, \
                 patch('primitive_vla_cobotmagic.ik.execution.MotionBridge.action') as action:
                result = move_to_pixel('right', 320, 240, config=path)
                self.assertTrue(result['execution_ready'], result)
                self.assertEqual(camera.calls, 1)
                self.assertAlmostEqual(result['duration_s'], 5.)
                self.assertAlmostEqual(result['target_xyz_m'][2], .19)
                self.assertAlmostEqual(result['target_xyz_m'][1]-result['point_xyz_m'][1], .04)
                self.assertGreaterEqual(result['target_downward_angle_deg'], 45)
                self.assertLessEqual(result['target_downward_angle_deg'], 65)
                self.assertFalse(result['jaw_heading_preserved'])
                folder = Path(result['bundle'])
                frozen, chain = load_setup(folder/'hover.yaml')
                self.assertEqual(frozen['move_duration_s'], 5.)
                self.assertEqual(frozen['speed_percent'], 25)
                self.assertEqual(frozen['settle_timeout_s'], 2.)
                saved = json.loads((folder/'plan.json').read_text())
                # Later edits cannot change the already frozen command.
                path.write_text('invalid: new configuration\n')
                self.assertEqual(reviewed_plan(frozen, chain, saved, raw['positions']), saved)
                with redirect_stdout(StringIO()):
                    self.assertEqual(execute_bundle(folder), 0)
                socket.assert_not_called()
                action.assert_not_called()

    def test_pixel_cli_executes_same_bundle_only_after_space_with_fresh_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, raw, _ = fixture(tmp)
            standalone_config(path)
            camera = ReadOnlyBridge(raw)
            robot = FakeBridge(raw['positions'])
            robot.close = lambda: None
            clock = Clock()
            def execute_with_clock(folder, *, execute):
                with patch('primitive_vla_cobotmagic.ik.execution.time.monotonic', clock.monotonic), \
                     patch('primitive_vla_cobotmagic.ik.execution.time.sleep', clock.sleep):
                    return execute_bundle(folder, execute=execute)
            with patch('primitive_vla_cobotmagic.ik.pixel.CameraBridge', return_value=camera), \
                 patch('primitive_vla_cobotmagic.ik.execution.MotionBridge', return_value=robot), \
                 patch('primitive_vla_cobotmagic.ik.execution.wait_for_space') as gate, \
                 patch('primitive_vla_cobotmagic.ik.pixel.execute_bundle', side_effect=execute_with_clock), \
                 patch('socket.create_connection') as socket, \
                 redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                gate.side_effect = lambda: self.assertEqual(robot.commands, [])
                code = main(['--config',str(path),'--arm','left','--pixel','320','240','--execute'])
            self.assertEqual(code, 0)
            gate.assert_called_once()
            self.assertTrue(robot.commands)
            socket.assert_not_called()
            self.assertEqual(camera.calls, 1)

    def test_invalid_settings_refuse_before_reading_camera(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _, _ = fixture(tmp)
            good = standalone_config(path)
            for field, value in [('move_duration_s', 0), ('downward_angle_range_deg', [80,40]),
                                 ('jaw_heading_tolerance_deg', float('nan')),
                                 ('feedback_limit_tolerance_deg', -1), ('feedback_limit_tolerance_deg', 3)]:
                bad = {**good, 'motion': {**good['motion'], field:value}}
                path.write_text(yaml.safe_dump(bad))
                with self.subTest(field=field), patch('primitive_vla_cobotmagic.ik.pixel.CameraBridge') as camera:
                    with self.assertRaises(ValueError):
                        PixelIK(path)
                    camera.assert_not_called()

    def test_redirect_resolves_paths_from_real_config_and_rejects_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _, _ = fixture(tmp)
            standalone_config(path)
            alias = Path(tmp)/'alias.yaml'
            alias.write_text('extends: web.yaml\n')
            actual, options = load_pixel_settings(alias)
            self.assertEqual(actual, path.resolve())
            self.assertEqual(options.target_base_z_m, .19)
            alias.write_text('extends: alias.yaml\n')
            with self.assertRaisesRegex(ValueError, 'Circular'):
                load_pixel_settings(alias)

    def test_cli_requires_explicit_arm_and_does_not_offer_web_motion(self):
        for argv in (['--pixel','320','240'], ['--web','--execute']):
            with self.subTest(argv=argv), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                main(argv)


if __name__ == '__main__':
    unittest.main()
