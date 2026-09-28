"""Replay measured zero-boundary feedback without any robot connection."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import yaml

from primitive_vla_cobotmagic.ik.execution import (
    BRIDGE_LIMITS, current_state_command, decode_positions, load_setup, make_plan,
)
import test_hover
from test_hover import ROOT, URDF, FakeBridge, snapshot


class FeedbackBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.case = json.loads((ROOT/'tests/fixtures/ik_zero_boundary_feedback.json').read_text())
        self.positions = self.case['positions']

    def setup_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'hover.yaml'
            path.write_text(yaml.safe_dump({**self.case['config'], 'urdf':str(URDF)}))
            return load_setup(path)

    def test_recorded_start_plans_and_executes_on_fake_bridge(self):
        config, chain = self.setup_chain()
        original = deepcopy(self.positions)
        plan = make_plan(config, chain, self.positions)
        self.assertEqual(self.positions, original)
        self.assertEqual(plan['start_positions'], original)
        path = np.asarray(plan['joint_path_rad'])
        self.assertTrue(np.all(path >= chain.lower))
        self.assertTrue(np.all(path <= chain.upper))
        self.assertAlmostEqual(plan['nominal_duration_s'], 3.)
        self.assertGreaterEqual(plan['target_downward_angle_deg'], 40.)
        self.assertLess(np.linalg.norm(chain.fk(path[-1])[:3,3]-plan['target_tcp_xyz_m']), .001)
        for row in path:
            self.assertLessEqual(abs(chain.jaw_heading_error(chain.fk(row), plan['jaw_heading_rad'])), np.deg2rad(2))
        bridge = FakeBridge(self.positions)
        result = test_hover.HoverTests.run_fake(self, bridge, config, chain, plan)
        self.assertLess(result['position_error_m'], .008)
        for command in bridge.commands:
            for side in ('left','right'):
                self.assertTrue(np.all(np.asarray(command[side][:6]) >= BRIDGE_LIMITS[:,0]))
                self.assertTrue(np.all(np.asarray(command[side][:6]) <= BRIDGE_LIMITS[:,1]))

    def test_recorded_reset_start_keeps_left_arm_and_continuous_three_second_path(self):
        self.case = json.loads((ROOT/'tests/fixtures/ik_after_reset_failure.json').read_text())
        self.positions = self.case['positions']
        config, chain = self.setup_chain()
        plan = make_plan(config, chain, self.positions)
        self.assertEqual(plan['arm'], 'left')
        path = np.asarray(plan['joint_path_rad'])
        self.assertAlmostEqual(plan['nominal_duration_s'], 3.)
        self.assertLessEqual(np.max(np.abs(np.diff(path, axis=0))), .00301)
        speeds = np.abs(np.diff(path, axis=0))/np.asarray(plan['durations_s'])[:, None]
        self.assertTrue(np.all(speeds <= chain.velocity+1e-8))
        for q in path:
            chain.check(q)
            self.assertLessEqual(abs(chain.jaw_heading_error(chain.fk(q), plan['jaw_heading_rad'])), np.deg2rad(2))
        final = chain.fk(path[-1])
        self.assertLess(np.linalg.norm(final[:3,3]-config['target_tcp_xyz_m']), .001)
        self.assertGreaterEqual(plan['target_downward_angle_deg'], 40.)
        result = test_hover.HoverTests.run_fake(self, FakeBridge(self.positions), config, chain, plan)
        self.assertLess(result['position_error_m'], .008)

    def test_feedback_stays_raw_but_hold_matches_existing_bridge_clamping(self):
        original = deepcopy(self.positions)
        self.assertEqual(decode_positions(snapshot(self.positions), .2), original)
        command = current_state_command(self.positions)
        for side in ('left','right'):
            np.testing.assert_array_equal(command[side][:6], np.clip(original[side][:6], BRIDGE_LIMITS[:,0], BRIDGE_LIMITS[:,1]))
        self.assertEqual(self.positions, original)

    def test_out_of_range_explicit_target_is_not_silently_clipped(self):
        with self.assertRaises(ValueError):
            current_state_command(self.positions, 'left', self.positions['left'][:6])

    def test_zero_tolerance_is_respected_by_decode_plan_and_command_conversion(self):
        config, chain = self.setup_chain()
        config['feedback_limit_tolerance_deg'] = 0
        for check in (
            lambda: decode_positions(snapshot(self.positions), .2, feedback_limit_tolerance_deg=0),
            lambda: current_state_command(self.positions, feedback_limit_tolerance_deg=0),
            lambda: make_plan(config, chain, self.positions),
        ):
            with self.assertRaises(ValueError):
                check()

    def test_larger_feedback_error_fails_before_angle_search_with_joint_detail(self):
        config, chain = self.setup_chain()
        self.positions['left'][2] = np.deg2rad(2.)
        with self.assertRaisesRegex(ValueError, 'left.*joint3'):
            decode_positions(snapshot(self.positions), .2)
        with patch('primitive_vla_cobotmagic.ik.execution._make_plan') as solve:
            with self.assertRaisesRegex(ValueError, 'joint3'):
                make_plan(config, chain, self.positions)
            solve.assert_not_called()


if __name__ == '__main__':
    unittest.main()
