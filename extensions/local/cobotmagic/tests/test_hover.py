"""Offline tests only. No sockets, real robot, enabling or motor commands."""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr

import numpy as np
import yaml

from primitive_vla_cobotmagic.ik.execution import (
    MotionBridge, decode_positions, execute_plan, load_setup, main, make_plan, current_state_command, gripper_hold_value,
    set_move_duration, wait_for_space,
)
from primitive_vla_cobotmagic.ik.kinematics import SerialIK, rotation, rotation_error, transform, downward_cone_error
from robot_fixture import robot_config


ROOT = Path(__file__).resolve().parents[1]
URDF = ROOT / "assets/reference/piper_description.urdf"


def setup():
    config = robot_config()
    # Arbitrary synthetic pose/fixture for numerical tests, NOT measured robot data.
    config.update(arm="left", urdf=str(URDF), tcp_calibration_result=None,
                  require_tcp_bounds=True, hover_height_m=.15, tcp_xyz_m=[0, 0, .1],
                  tcp_bounds_m=[[-1, -1, -1], [1, 1, 1]])
    chain = SerialIK(URDF, "base_link", "link6", config["joint_names"], transform([0, 0, .1], [0, 0, 0]))
    q = np.array([.1, .9, -.8, .2, .3, -.1])
    xyz = chain.fk(q)[:3, 3] + np.array([.003, 0, .003])
    config["bowl_rim_center_xyz_m"] = (xyz - [0, 0, config["hover_height_m"]]).tolist()
    positions = {"left": [*q.tolist(), .01], "right": [*q.tolist(), .02]}
    return config, chain, positions


def snapshot(positions, stamp=1_000_000_000):
    return {"ok": True, "timestamp_ns": stamp, "positions": positions,
            "joint_timestamps_ns": {name: stamp for name in positions}}


class FakeBridge:
    def __init__(self, positions, allow_motion=True, follows=True, arm="left"):
        self.positions = deepcopy(positions)
        self.allow_motion, self.follows = allow_motion, follows
        self.stamp = 1_000_000_000
        self.commands = []
        self.arm = arm

    def observation(self):
        self.stamp += 10_000_000
        return deepcopy(self.positions), snapshot(deepcopy(self.positions), self.stamp)

    def action(self, positions):
        self.commands.append(deepcopy(positions))
        if self.follows:
            self.positions = deepcopy(positions)


class DeadbandBridge(FakeBridge):
    """Models a driver ignoring setpoint changes smaller than 0.008 rad."""
    def action(self, positions):
        self.commands.append(deepcopy(positions))
        if np.max(np.abs(np.asarray(positions[self.arm][:6]) - self.positions[self.arm][:6])) >= .008:
            self.positions = deepcopy(positions)


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        self.now += .001
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class HoverTests(unittest.TestCase):
    def test_seven_recorded_wrist_start_failures_have_continuous_paths(self):
        cases = json.loads((ROOT/'tests/fixtures/ik_wrist_start_failures.json').read_text())
        for case in cases:
            with self.subTest(click=case['id']):
                config = {**case['config'], 'urdf': str(URDF)}
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory)/'config.yaml'
                    path.write_text(yaml.safe_dump(config))
                    config, chain = load_setup(path)
                plan = make_plan(config, chain, case['positions'])
                self.assertEqual(plan['path_mode'], 'joint_guided')
                path = np.asarray(plan['joint_path_rad'])
                self.assertLessEqual(np.max(np.abs(np.diff(path, axis=0))), .00301)
                self.assertTrue(np.all(path >= chain.lower-1e-7))
                self.assertTrue(np.all(path <= chain.upper+1e-7))
                speeds = np.abs(np.diff(path, axis=0))/np.asarray(plan['durations_s'])[:,None]
                self.assertTrue(np.all(speeds <= chain.velocity+1e-8))
                self.assertAlmostEqual(sum(plan['durations_s']), 3.)
                for q in path:
                    self.assertLessEqual(abs(chain.jaw_heading_error(chain.fk(q), plan['jaw_heading_rad'])), np.deg2rad(2))
                final = chain.fk(path[-1])
                self.assertLess(np.linalg.norm(final[:3,3]-config['target_tcp_xyz_m']), .001)
                self.assertLessEqual(chain.downward_tilt(final), np.deg2rad(50))
                result = self.run_fake(FakeBridge(case['positions']), config, chain, plan)
                self.assertLess(result['position_error_m'], .008)

    def test_downward_search_prefers_vertical_then_refines_highest_feasible_band(self):
        config, chain, positions = setup()
        config['orientation_mode'] = 'downward'
        for ceiling, expected in ((90, [90]), (83, [90, 85, 80, 84, 83]),
                                  (40, [90,85,80,75,70,65,60,55,50,45,40,44,43,42,41])):
            attempted = []
            def candidate(*args, downward_goal_deg):
                attempted.append(downward_goal_deg)
                if downward_goal_deg > ceiling:
                    raise ValueError('No continuous path for this angle')
                return {'downward_goal_deg': downward_goal_deg}
            with patch('primitive_vla_cobotmagic.ik.execution._make_plan', side_effect=candidate):
                self.assertEqual(make_plan(config, chain, positions)['downward_goal_deg'], ceiling)
            self.assertEqual(attempted, expected)
        with patch('primitive_vla_cobotmagic.ik.execution._make_plan', side_effect=ValueError('no path')) as planner:
            with self.assertRaisesRegex(ValueError, '40–90'):
                make_plan(config, chain, positions)
            self.assertEqual(min(call.kwargs['downward_goal_deg'] for call in planner.call_args_list), 40)

    def test_downward_cone_at_17cm_for_recorded_left_and_right_starts(self):
        for name in ('ik_startup_lag', 'ik_three_seconds'):
            with self.subTest(fixture=name):
                fixture = json.loads((ROOT / f'tests/fixtures/{name}.json').read_text())
                config, _, _ = setup()
                config.update(fixture['config'], orientation_mode='downward', move_duration_s=3.,
                              urdf=str(URDF), tcp_calibration_result=None)
                config['target_tcp_xyz_m'][2] = .17
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'config.yaml'
                    path.write_text(yaml.safe_dump(config))
                    config, chain = load_setup(path)
                plan = make_plan(config, chain, fixture['positions'])
                final = chain.fk(plan['joint_path_rad'][-1])
                # The two-finger line must retain its original XY heading,
                # including throughout the actual joint-interpolated path.
                axes = np.array([chain.fk(q)[:3, :3] @ chain.tcp[:3, :3].T[:, 1]
                                 for q in plan['joint_path_rad']])
                headings = np.arctan2(axes[:, 1], axes[:, 0])
                deviations = np.arctan2(np.sin(headings-headings[0]), np.cos(headings-headings[0]))
                self.assertLess(np.max(np.abs(deviations)), np.deg2rad(2))
                self.assertAlmostEqual(sum(plan['durations_s']), 3.)
                self.assertLess(np.linalg.norm(final[:3, 3] - config['target_tcp_xyz_m']), .001)
                self.assertLessEqual(chain.downward_tilt(final), np.deg2rad(50))
                self.assertGreaterEqual(plan['target_downward_angle_deg'], 40)
                self.assertLessEqual(plan['target_downward_angle_deg'], 90)
                self.assertEqual(plan['orientation_mode'], 'downward')
                self.assertEqual(plan['downward_angle_range_deg'], [40, 90])
                self.assertEqual(plan['preferred_downward_angle_deg'], 90)
                self.assertGreaterEqual(plan['target_downward_angle_deg'] + .01, plan['downward_goal_deg'])
                self.assertLessEqual(np.max(np.abs(np.diff(plan['joint_path_rad'], axis=0))), .00301)
                result = self.run_fake(FakeBridge(fixture['positions']), config, chain, plan)
                self.assertGreaterEqual(result['downward_angle_deg'], 40)
                self.assertLess(abs(result['jaw_heading_error_deg']), 2)

    def test_jaw_heading_wrap_and_calibrated_tcp_frame(self):
        _, chain, _ = setup()
        chain.tcp = transform([.01, -.01, .15], [.4, -.7, .2])
        # With roll=pi and yaw=-89 degrees, the directed finger line is at -179.
        tip = transform([.3, 0, .17], [np.pi, 0, np.deg2rad(-89)])
        pose = tip @ chain.tcp
        self.assertAlmostEqual(np.rad2deg(chain.jaw_heading(pose)), -179)
        self.assertAlmostEqual(np.rad2deg(chain.jaw_heading_error(pose, np.deg2rad(179))), -2)
        rotated = transform([.3, 0, .17], [np.pi, 0, np.deg2rad(1)]) @ chain.tcp
        self.assertAlmostEqual(abs(np.rad2deg(chain.jaw_heading_error(rotated, chain.jaw_heading(pose)))), 90)
        vertical_jaws = transform([0, 0, 0], [np.pi/2, 0, 0]) @ chain.tcp
        with self.assertRaisesRegex(ValueError, '水平朝向'):
            chain.jaw_heading(vertical_jaws)

    def test_downward_cone_uses_physical_axis_not_rpy_pitch_or_tcp_rotation(self):
        _, chain, _ = setup()
        for tilt in (0, 19, 21, 90, 180):
            axis = np.array([np.sin(np.deg2rad(tilt)), 0., -np.cos(np.deg2rad(tilt))])
            error = downward_cone_error(axis, np.deg2rad(20))
            self.assertTrue(np.isfinite(error).all())
            self.assertAlmostEqual(np.linalg.norm(error), np.deg2rad(max(0, tilt-20)))
        # Changing a calibrated TCP frame must not redefine the gripper's axis.
        tip = transform([.3, 0, .17], [np.pi, 0, .7])
        chain.tcp = transform([0, 0, .1], [.4, -.7, .2])
        self.assertAlmostEqual(chain.downward_tilt(tip @ chain.tcp), 0., places=6)

    def test_recorded_startup_lag_does_not_abort_a_moving_arm(self):
        fixture = json.loads((ROOT / 'tests/fixtures/ik_startup_lag.json').read_text())
        config = fixture['config']
        config['urdf'] = str(URDF)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.yaml'
            path.write_text(yaml.safe_dump(config))
            config, chain = load_setup(path)
        plan = make_plan(config, chain, fixture['positions'])
        clock, events = Clock(), []

        class LaggedBridge(FakeBridge):
            def __init__(self):
                super().__init__(fixture['positions'], arm='left')
                self.prefix = iter(fixture['feedback_prefix'])
                self.pending = []
                self.first = True

            def action(self, positions):
                self.commands.append(deepcopy(positions))
                self.pending.append((clock.now + .12, deepcopy(positions)))

            def observation(self):
                if self.first:
                    self.first = False
                    return super().observation()
                recorded = next(self.prefix, None)
                if recorded is not None:
                    clock.now = max(clock.now, recorded['elapsed_s'] + .002)
                    self.positions = deepcopy(recorded['positions'])
                else:
                    while self.pending and self.pending[0][0] <= clock.now:
                        _, self.positions = self.pending.pop(0)
                return super().observation()

        bridge = LaggedBridge()
        with patch('primitive_vla_cobotmagic.ik.execution.time.monotonic', clock.monotonic), \
             patch('primitive_vla_cobotmagic.ik.execution.time.sleep', clock.sleep), redirect_stderr(io.StringIO()):
            result = execute_plan(bridge, config, chain, plan, on_event=events.append)
        self.assertGreater(max(e['tracking_error_rad'] for e in events if e['event']=='feedback'), .04)
        self.assertLess(result['position_error_m'], .008)
        self.assertLess(clock.now, 6.)
        self.assertTrue(any(e['event']=='completed' for e in events))

    def test_reported_slow_path_can_be_planned_for_three_seconds(self):
        fixture = json.loads((ROOT / 'tests/fixtures/ik_three_seconds.json').read_text())
        config, _, _ = setup()
        config.update(fixture['config'], move_duration_s=3.)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.yaml'
            path.write_text(yaml.safe_dump(config))
            config, chain = load_setup(path)
        plan = make_plan(config, chain, fixture['positions'])
        self.assertAlmostEqual(sum(plan['durations_s']), 3.)
        speeds = np.abs(np.diff(plan['joint_path_rad'], axis=0)) / np.array(plan['durations_s'])[:, None]
        self.assertLessEqual(speeds.max(), config['max_joint_speed_rad_s'] + 1e-9)
        self.assertTrue(np.all(speeds <= chain.velocity + 1e-9))
        self.assertGreater(config['speed_percent'], 5)
        bridge = DeadbandBridge(fixture['positions'], arm='right')
        result = self.run_fake(bridge, config, chain, plan)
        self.assertLess(result['position_error_m'], .008)
        chain.velocity[:] = .1
        with self.assertRaisesRegex(ValueError, 'URDF'):
            set_move_duration(plan, config, chain, 3.)

    def test_requested_duration_preserves_path_and_rejects_excess_speed(self):
        config, chain, positions = setup()
        original = make_plan(config, chain, positions)
        plan = set_move_duration(original, config, chain, 10.)
        self.assertEqual(plan['joint_path_rad'], original['joint_path_rad'])
        self.assertAlmostEqual(sum(plan['durations_s']), 10.)
        self.assertAlmostEqual(original['nominal_duration_s'], 3.)
        speed = np.abs(np.diff(plan['joint_path_rad'], axis=0)) / np.array(plan['durations_s'])[:, None]
        self.assertLessEqual(speed.max(), config['max_joint_speed_rad_s'])
        for duration in (.00001, 0, -1, float('nan'), float('inf')):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                set_move_duration(original, config, chain, duration)

    def test_space_gate_ignores_enter_restores_terminal_and_rejects_pipes(self):
        from unittest.mock import Mock
        stdin = Mock()
        stdin.isatty.return_value = True
        stdin.fileno.return_value = 7
        for keys, cancelled in [(['\n','x',' '],False), (['\x1b'],True)]:
            stdin.read.side_effect = keys
            with patch('primitive_vla_cobotmagic.ik.execution.sys.stdin',stdin), \
                 patch('primitive_vla_cobotmagic.ik.execution.termios.tcgetattr',return_value=['original']), \
                 patch('primitive_vla_cobotmagic.ik.execution.tty.setcbreak'), \
                 patch('primitive_vla_cobotmagic.ik.execution.termios.tcflush') as flush, \
                 patch('primitive_vla_cobotmagic.ik.execution.termios.tcsetattr') as restore, redirect_stdout(io.StringIO()):
                if cancelled:
                    with self.assertRaises(KeyboardInterrupt): wait_for_space()
                else:
                    wait_for_space()
                flush.assert_called_once()
                self.assertEqual(restore.call_args.args[2], ['original'])
        stdin.isatty.return_value = False
        with patch('primitive_vla_cobotmagic.ik.execution.sys.stdin',stdin), self.assertRaises(ValueError):
            wait_for_space()

    def test_cli_cancelling_space_gate_or_moving_while_waiting_sends_nothing(self):
        config, _, positions = setup()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'config.yaml'; path.write_text(yaml.safe_dump(config))
            for cancel in (True, False):
                fake = FakeBridge(positions)
                fake.close = lambda: None
                def wait():
                    self.assertEqual(fake.commands, [])
                    if cancel: raise KeyboardInterrupt
                    fake.positions['left'][0] += .03
                with patch('primitive_vla_cobotmagic.ik.execution.MotionBridge',return_value=fake), \
                     patch('primitive_vla_cobotmagic.ik.execution.wait_for_space',side_effect=wait), \
                     redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    result = main(['--config',str(path),'--output',str(root/'plan.json'),'--execute'])
                self.assertEqual(result,130 if cancel else 1)
                self.assertEqual(fake.commands, [])

    def test_streamed_path_passes_driver_deadband_without_waiting_at_each_tiny_point(self):
        fixture = json.loads((ROOT / 'tests/fixtures/ik_near_limits.json').read_text())
        config = fixture['config']
        config['urdf'] = str(URDF)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.yaml'
            path.write_text(yaml.safe_dump(config))
            config, chain = load_setup(path)
        plan = make_plan(config, chain, fixture['positions'])
        bridge = DeadbandBridge(fixture['positions'], arm='right')
        result = self.run_fake(bridge, config, chain, plan)
        self.assertLess(result['position_error_m'], .008)
        self.assertLess(result['orientation_error_rad'], .04)

    def test_current_state_command_changes_only_selected_arm_and_normalizes_gripper(self):
        _, _, positions = setup()
        positions['right'][6] = -.0043
        original = deepcopy(positions)
        goal = [q + .001 for q in positions['left'][:6]]
        command = current_state_command(positions, 'left', goal)
        self.assertEqual(command['left'][:6], goal)
        self.assertEqual(command['right'][:6], positions['right'][:6])
        self.assertEqual(command['left'][6], positions['left'][6])
        self.assertEqual(command['right'][6], 0.0)
        self.assertEqual(positions, original)

    def test_existing_send_action_packet_and_echo_without_capability_handshake(self):
        config, _, positions = setup()
        positions['right'][6] = -.0043
        expected = current_state_command(positions)
        bridge = MotionBridge(config, allow_motion=True)
        with patch.object(bridge, 'request', return_value={'ok': True, 'positions': expected}) as request:
            bridge.action(positions)
            self.assertEqual(request.call_args.args[0], {
                'op': 'send_action', 'positions': expected, 'speed_percent': config['speed_percent']})
        altered = deepcopy(expected)
        altered['left'][0] += .1
        with patch.object(bridge, 'request', return_value={'ok': True, 'positions': altered}):
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                bridge.action(positions)

    def test_position_only_real_click_from_near_limit_start(self):
        fixture = json.loads((ROOT / 'tests/fixtures/ik_near_limits.json').read_text())
        config = fixture['config']
        config['urdf'] = str(URDF)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.yaml'
            path.write_text(yaml.safe_dump(config))
            loaded, chain = load_setup(path)
        plan = make_plan(loaded, chain, fixture['positions'])
        final = chain.fk(plan['joint_path_rad'][-1])
        self.assertLess(np.linalg.norm(final[:3, 3] - config['target_tcp_xyz_m']), .001)
        self.assertLess(np.max(np.abs(np.diff(plan['joint_path_rad'], axis=0))), .00301)
        for q in plan['joint_path_rad']:
            chain.check(q)
        self.assertFalse(plan['collision_checked'])
        self.assertEqual(plan['orientation_mode'], 'position')
        # Explicitly constrained orientation must never silently become free.
        loaded.update(orientation_mode='fixed', target_rpy_rad=[0, 0, 0])
        with self.assertRaises(ValueError):
            make_plan(loaded, chain, fixture['positions'])

    def test_calibrated_tcp_import_rejects_mismatch_and_stale_samples(self):
        config, _, _ = setup()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "samples/one").mkdir(parents=True)
            (root / "samples/one/sample.json").write_text("{}")
            (root / "manifest.json").write_text(json.dumps({"config": config}))
            result = {"phase": "tcp", "accepted": True, "arm": "left",
                      "base_frame": "base_link", "tip_frame": "link6",
                      "urdf_sha256": hashlib.sha256(URDF.read_bytes()).hexdigest(),
                      "training_ids": ["one"], "validation_ids": [],
                      "tcp_xyz_m": [.007, .001, .155], "tcp_rpy_rad": [0, 0, 0]}
            config.update(tcp_xyz_m=None, tcp_calibration_result="result.json")
            path = root / "config.yaml"
            path.write_text(yaml.safe_dump(config))
            (root / "result.json").write_text(json.dumps(result))
            loaded, chain = load_setup(path)
            np.testing.assert_allclose(chain.tcp[:3, 3], result["tcp_xyz_m"])
            self.assertFalse(loaded["preview_missing_fields"])
            for key, value in (("arm", "right"), ("accepted", False),
                               ("urdf_sha256", "different"), ("tip_frame", "other"),
                               ("training_ids", ["missing"])):
                changed = {**result, key: value}
                (root / "result.json").write_text(json.dumps(changed))
                with self.subTest(key=key), self.assertRaises(ValueError):
                    load_setup(path)

    def test_explicitly_optional_bounds_allow_plan_but_provided_bounds_still_check(self):
        config, _, positions = setup()
        config.update(require_tcp_bounds=False, tcp_bounds_m=None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump(config))
            loaded, chain = load_setup(path)
            plan = make_plan(loaded, chain, positions)
            self.assertFalse(plan["preview_only"])
            self.assertFalse(plan["tcp_bounds_checked"])
            loaded["tcp_bounds_m"] = [[0, 0, 0], [.001, .001, .001]]
            with self.assertRaisesRegex(ValueError, "workspace envelope"):
                make_plan(loaded, chain, positions)

    def test_negative_gripper_feedback_is_preserved_not_command_validated(self):
        _, _, positions = setup()
        positions["right"][6] = -.0043
        self.assertEqual(decode_positions(snapshot(positions), .2)["right"][6], -.0043)

    def test_fk_rotation_conventions_and_pi_rotation(self):
        actual = transform([1, 2, 3], [0, 0, np.pi / 2]) @ [1, 0, 0, 1]
        np.testing.assert_allclose(actual, [1, 3, 3, 1], atol=1e-10)
        r = rotation([0, 0, 1], np.pi)
        error = rotation_error(r, np.eye(3))
        self.assertAlmostEqual(np.linalg.norm(error), np.pi)

    def test_both_official_models_roundtrip_ik_and_limits(self):
        config, _, positions = setup()
        q = np.array(positions["left"][:6])
        for filename in ("piper_description.urdf", "piper_description_old.urdf"):
            chain = SerialIK(URDF.with_name(filename), "base_link", "link6",
                             config["joint_names"], np.eye(4))
            target = chain.fk(q + [.01, .02, -.02, .01, .01, -.01])
            result = chain.solve(target, q)
            self.assertLess(np.linalg.norm(chain.fk(result)[:3, 3] - target[:3, 3]), .001)
            self.assertLess(np.linalg.norm(rotation_error(target[:3, :3], chain.fk(result)[:3, :3])), .015)
            with self.assertRaises(ValueError):
                chain.check([10, 0, 0, 0, 0, 0])

    def test_unreachable_pose_and_invalid_joint_mapping_rejected(self):
        config, chain, positions = setup()
        with self.assertRaises(ValueError):
            chain.solve(transform([20, 0, 0], [0, 0, 0]), positions["left"][:6])
        with self.assertRaises(ValueError):
            SerialIK(URDF, "base_link", "link6", ["joint1"] * 6, np.eye(4))

    def test_plan_hover_height_joint_steps_and_workspace(self):
        config, chain, positions = setup()
        plan = make_plan(config, chain, positions)
        expected = np.array(config["bowl_rim_center_xyz_m"]) + [0, 0, .15]
        np.testing.assert_allclose(plan["target_tcp_xyz_m"], expected)
        steps = np.diff(plan["joint_path_rad"], axis=0)
        self.assertLessEqual(np.max(np.abs(steps)), .003 + 1e-9)
        self.assertFalse(plan["collision_checked"])
        config["tcp_bounds_m"] = [[0, 0, 0], [.001, .001, .001]]
        with self.assertRaises(ValueError):
            make_plan(config, chain, positions)

    def test_feedback_rejects_stale_nonfinite_missing_and_future(self):
        _, _, positions = setup()
        for kind in ("stale", "nan", "missing", "future", "transport"):
            reply = snapshot(deepcopy(positions))
            if kind == "stale": reply["joint_timestamps_ns"]["left"] -= 500_000_000
            if kind == "future": reply["joint_timestamps_ns"]["left"] += 100_000_000
            if kind == "nan": reply["positions"]["right"][0] = float("nan")
            if kind == "missing": del reply["joint_timestamps_ns"]["right"]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                decode_positions(reply, .2, .3 if kind == "transport" else 0)

    def test_control_gate_happens_before_socket(self):
        config, _, _ = setup()
        with patch("socket.create_connection") as connect:
            with self.assertRaises(RuntimeError):
                MotionBridge(config).request({"op": "send_action"})
            with self.assertRaises(ValueError):
                MotionBridge(config, True).request({"op": "enable"})
            connect.assert_not_called()

    def test_unfilled_config_refuses_before_connection(self):
        config, _, _ = setup()
        config.update(tcp_xyz_m=None, tcp_bounds_m=None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump(config))
            with patch("socket.create_connection") as connect, redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--config", str(path), "--execute"]), 1)
                connect.assert_not_called()

    def test_missing_tcp_and_bounds_allow_labelled_read_only_preview(self):
        config, chain, positions = setup()
        chain.tcp = np.eye(4)
        config.update(tcp_xyz_m=None, tcp_bounds_m=None,
            bowl_rim_center_xyz_m=(chain.fk(positions["left"][:6])[:3, 3] + [.003, 0, -.147]).tolist())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, output = root / "config.yaml", root / "preview.json"
            path.write_text(yaml.safe_dump(config))
            original = path.read_bytes()
            with patch("primitive_vla_cobotmagic.ik.execution.MotionBridge") as factory, \
                 redirect_stdout(io.StringIO()) as stdout:
                bridge = factory.return_value
                bridge.observation.return_value = (positions, snapshot(positions))
                result = main(["--config", str(path), "--output", str(output)])
                self.assertEqual(result, 0)
                self.assertFalse(factory.call_args.kwargs["allow_motion"])
                bridge.action.assert_not_called()
            plan = json.loads(output.read_text())
            self.assertTrue(plan["preview_only"])
            self.assertEqual(plan["reference_point"], "link6 origin (TCP unknown)")
            self.assertFalse(plan["tcp_bounds_checked"])
            self.assertIn("tcp_xyz_m", stdout.getvalue())
            self.assertEqual(path.read_bytes(), original)
            fake = FakeBridge(positions)
            with self.assertRaisesRegex(ValueError, "preview"):
                self.run_fake(fake, config, chain, plan)
            self.assertEqual(fake.commands, [])

    def test_offline_cli_outputs_plan_and_cannot_execute(self):
        config, _, positions = setup()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.yaml").write_text(yaml.safe_dump(config))
            (root / "snapshot.json").write_text(json.dumps(snapshot(positions)))
            args = ["--config", str(root / "config.yaml"), "--snapshot", str(root / "snapshot.json"),
                    "--output", str(root / "plan.json")]
            with patch("socket.create_connection") as connect, redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertTrue((root / "plan.json").is_file())
                connect.assert_not_called()
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                main([*args, "--execute"])

    def test_preview_keeps_provided_tcp_and_checks_provided_bounds(self):
        config, _, positions = setup()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            config["tcp_bounds_m"] = None
            path.write_text(yaml.safe_dump(config))
            loaded, chain = load_setup(path, preview=True)
            self.assertEqual(loaded["tcp_xyz_m"], [0, 0, .1])
            self.assertEqual(loaded["reference_point"], "configured TCP")
            plan = make_plan(loaded, chain, positions)
            self.assertTrue(plan["preview_only"])
            self.assertEqual(plan["missing_execution_fields"], ["tcp_bounds_m"])
            config.update(tcp_xyz_m=None, tcp_bounds_m=[[0, 0, 0], [.001, .001, .001]])
            path.write_text(yaml.safe_dump(config))
            loaded, chain = load_setup(path, preview=True)
            with self.assertRaisesRegex(ValueError, "workspace envelope"):
                make_plan(loaded, chain, positions)

    def run_fake(self, bridge, config, chain, plan):
        clock = Clock()
        with patch("primitive_vla_cobotmagic.ik.execution.time.monotonic", clock.monotonic), \
             patch("primitive_vla_cobotmagic.ik.execution.time.sleep", clock.sleep), redirect_stderr(io.StringIO()):
            return execute_plan(bridge, config, chain, plan)

    def test_feedback_execution_preserves_other_arm_and_bridge_gripper_hold(self):
        config, chain, positions = setup()
        positions["left"][6], positions["right"][6] = -.0043, -.002
        plan = make_plan(config, chain, positions)
        bridge = FakeBridge(positions)
        result = self.run_fake(bridge, config, chain, plan)
        self.assertLess(result["position_error_m"], .008)
        self.assertTrue(bridge.commands)
        for command in bridge.commands:
            self.assertEqual(command["right"][:6], positions["right"][:6])
            for side in ("left", "right"):
                self.assertEqual(command[side][6], gripper_hold_value(positions[side][6]))
        self.assertEqual(positions["left"][6], -.0043)
        self.assertEqual(positions["right"][6], -.002)

    def test_changed_start_and_disabled_execution_send_nothing(self):
        config, chain, positions = setup()
        plan = make_plan(config, chain, positions)
        for disabled in (True, False):
            bridge = FakeBridge(positions, allow_motion=not disabled)
            if not disabled: bridge.positions["left"][0] += .1
            with self.assertRaises((ValueError, RuntimeError)):
                self.run_fake(bridge, config, chain, plan)
            self.assertEqual(bridge.commands, [])

    def test_command_echo_cannot_count_as_arrival(self):
        config, chain, positions = setup()
        # Larger movement ensures an immobile robot cannot pass final pose verification.
        config["bowl_rim_center_xyz_m"][2] += .025
        plan = make_plan(config, chain, positions)
        bridge = FakeBridge(positions, follows=False)
        with self.assertRaises((TimeoutError, RuntimeError)):
            self.run_fake(bridge, config, chain, plan)

    def test_stationary_feedback_logs_failure_and_hold_instead_of_claiming_arrival(self):
        config, chain, positions = setup()
        config['bowl_rim_center_xyz_m'][2] += .025
        plan = make_plan(config, chain, positions)
        bridge = FakeBridge(positions, follows=False)
        clock, events = Clock(), []
        with patch('primitive_vla_cobotmagic.ik.execution.time.monotonic', clock.monotonic), \
             patch('primitive_vla_cobotmagic.ik.execution.time.sleep', clock.sleep), redirect_stderr(io.StringIO()):
            with self.assertRaises((TimeoutError, RuntimeError)):
                execute_plan(bridge, config, chain, plan, on_event=events.append)
        failed = next(e for e in events if e['event'] == 'failed')
        self.assertIn('终点反馈', failed['error'])
        self.assertGreater(failed['elapsed_s'], plan['nominal_duration_s'] + 3)
        self.assertIn('最大关节变化 0.00°', failed['error'])
        self.assertIn('goal_rad', failed)
        self.assertEqual(failed['positions'], positions)
        self.assertTrue(any(e['event'] == 'command_ack' for e in events))
        self.assertFalse(any(e['event'] == 'completed' for e in events))
        self.assertEqual(events[-1]['event'], 'hold_ack')


if __name__ == "__main__":
    unittest.main()
