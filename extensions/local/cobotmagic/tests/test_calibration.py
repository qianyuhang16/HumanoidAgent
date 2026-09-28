"""Entirely offline: synthetic ChArUco images, known transforms and fake ROS replies."""

import base64
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import cv2
import numpy as np
import httpx

from primitive_vla_cobotmagic.calibration.core import (
    Board, Collector, Dataset, DEFAULT_CONFIG, board_pose, fit_handeye,
    read_config, solve_session, write_json,
)
from primitive_vla_cobotmagic.calibration.web import create_app
from primitive_vla_cobotmagic.ik.kinematics import SerialIK, transform


def raster(board, pose, k, size=(1280, 720)):
    options = board.options
    source = board.board.generateImage((options["squares_x"] * 160, options["squares_y"] * 160), marginSize=0)
    pixels_per_m = 160 / options["square_m"]
    h = k @ np.column_stack([pose[:3, 0] / pixels_per_m, pose[:3, 1] / pixels_per_m, pose[:3, 3]])
    image = cv2.warpPerspective(source, h, size, borderValue=255)
    return cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 98])[1].tobytes()


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = read_config(DEFAULT_CONFIG)
        self.board = Board(self.config["board"])
        self.k = np.array([[900., 0, 640], [0, 900, 360], [0, 0, 1]])
        self.intrinsics = {"accepted": True, "camera": "front", "distortion_model": "opencv_pinhole",
                           "K": self.k.tolist(), "D": [0.] * 5, "image_size": [1280, 720]}

    def reply(self, stamp=10_000_000_000):
        jpeg = raster(self.board, transform([-.075, -.105, .7], [.15, -.1, .04]), self.k)
        return {"ok": True, "timestamp_ns": stamp,
            "images": {"front": base64.b64encode(jpeg).decode()},
            "image_timestamps_ns": {"front": stamp - 10_000_000},
            "joint_timestamps_ns": {"right": stamp - 5_000_000},
            "positions": {"right": [.1, .8, -.8, .1, .3, -.1, .02]}}

    def test_board_export_is_detectable_and_physical_size_is_explicit(self):
        self.board.export(self.root)
        svg = (self.root / "board-a4.svg").read_text()
        self.assertIn('width="210mm" height="297mm"', svg)
        image = cv2.imread(str(self.root / "board.png"))
        jpeg = cv2.imencode('.jpg', image)[1].tobytes()
        detected, _ = self.board.detect(jpeg)
        self.assertEqual(len(detected["ids"]), 24)

    def test_capture_roundtrip_duplicate_and_session_consistency(self):
        dataset = Dataset(self.root / "session", self.config, "intrinsics")
        collector = Collector(dataset)
        collector.ingest(self.reply(), .001)
        self.assertTrue(collector.state()["ready"])
        sample_id = collector.record("train")
        saved = json.loads((dataset.folder / "samples" / sample_id / "sample.json").read_text())
        self.assertEqual(saved["split"], "train")
        self.assertEqual(saved["joints_rad"], [.1, .8, -.8, .1, .3, -.1])
        self.assertAlmostEqual(saved["timestamp_skew_s"], .005)
        with self.assertRaisesRegex(ValueError, "already"):
            collector.record("validation")
        self.assertEqual(len(Dataset(dataset.folder, self.config, "intrinsics").samples), 1)
        changed = deepcopy(self.config)
        changed["board"]["square_m"] = .031
        with self.assertRaisesRegex(ValueError, "changed"):
            Dataset(dataset.folder, changed, "intrinsics")

    def test_stale_skew_invalid_feedback_and_offline_recording_rejected(self):
        dataset = Dataset(self.root / "session", self.config, "intrinsics")
        collector = Collector(dataset)
        with self.assertRaisesRegex(ValueError, "ROS"):
            collector.record("train")
        for change in ("stale", "skew", "nan", "transport"):
            reply = self.reply()
            if change == "stale": reply["image_timestamps_ns"]["front"] -= 1_000_000_000
            if change == "skew":
                reply["joint_timestamps_ns"]["right"] = reply["image_timestamps_ns"]["front"] - int((self.config["capture"]["max_skew_s"] + .02) * 1e9)
            if change == "nan": reply["positions"]["right"][0] = float('nan')
            with self.subTest(change=change), self.assertRaises(ValueError):
                collector.ingest(reply, .6 if change == "transport" else .001)
        collector.ingest(self.reply(), .001)
        with patch("primitive_vla_cobotmagic.calibration.core.time.monotonic", return_value=collector.latest["received"] + 2):
            self.assertFalse(collector.state()["ready"])
            with self.assertRaisesRegex(ValueError, "过期"):
                collector.record("train")

    def test_handeye_capture_requires_settled_fresh_joints_and_stores_fk(self):
        dataset = Dataset(self.root / "session", self.config, "handeye", self.intrinsics)
        collector = Collector(dataset)
        with patch("primitive_vla_cobotmagic.calibration.core.time.monotonic", return_value=0):
            collector.ingest(self.reply(), .001)
            self.assertFalse(collector.state()["ready"])

        for i in range(1, 7):
            with patch("primitive_vla_cobotmagic.calibration.core.time.monotonic", return_value=i*.2):
                collector.ingest(self.reply(10_000_000_000 + i*200_000_000), .001)
        with patch("primitive_vla_cobotmagic.calibration.core.time.monotonic", return_value=1.2):
            self.assertTrue(collector.state()["ready"], collector.message)
            sample_id = collector.record("train")
        saved = json.loads((dataset.folder / 'samples' / sample_id / 'sample.json').read_text())
        self.assertIn("T_base_from_tip", saved)
        self.assertIn("T_camera_from_board", saved)
        self.assertLess(saved['reprojection_px'], 1)
        moving = self.reply(11_400_000_000)
        moving["positions"]["right"][0] += .05
        with patch("primitive_vla_cobotmagic.calibration.core.time.monotonic", return_value=1.4):
            collector.ingest(moving, .001)
            self.assertFalse(collector.state()["ready"])

    def test_400ms_skew_is_accepted_without_bypassing_settle_or_age_checks(self):
        collector = Collector(Dataset(self.root / 'relaxed', self.config, 'handeye', self.intrinsics))
        for index in range(7):
            reply = self.reply(10_000_000_000 + index*200_000_000)
            reply['image_timestamps_ns']['front'] = reply['joint_timestamps_ns']['right'] - 400_000_000
            with patch('primitive_vla_cobotmagic.calibration.core.time.monotonic', return_value=index*.2):
                collector.ingest(reply, .001)
                if index == 0: self.assertFalse(collector.state()['ready'])
        with patch('primitive_vla_cobotmagic.calibration.core.time.monotonic', return_value=1.2):
            self.assertTrue(collector.state()['ready'], collector.message)
            self.assertAlmostEqual(collector.latest['sample']['timestamp_skew_s'], .4)
        with patch('primitive_vla_cobotmagic.calibration.core.time.monotonic', return_value=2.0):
            self.assertFalse(collector.state()['ready'])

    def test_capture_threshold_change_resumes_old_session_with_per_sample_policy(self):
        old_config = deepcopy(self.config)
        old_config['capture']['max_skew_s'] = .08
        original = Dataset(self.root / 'resume', old_config, 'intrinsics')
        before = original.manifest_path.read_bytes()
        collector = Collector(original)
        collector.ingest(self.reply(), .001)
        old_id = collector.record('train')
        new_config = deepcopy(old_config)
        new_config['capture']['max_skew_s'] = .4
        resumed = Dataset(original.folder, new_config, 'intrinsics')
        self.assertEqual(len(resumed.samples), 1)
        collector = Collector(resumed)
        reply = self.reply(11_000_000_000)
        jpeg = raster(self.board, transform([-.03, -.105, .7], [.15, -.1, .04]), self.k)
        reply['images']['front'] = base64.b64encode(jpeg).decode()
        reply['image_timestamps_ns']['front'] = reply['joint_timestamps_ns']['right'] - 400_000_000
        collector.ingest(reply, .001)
        new_id = collector.record('validation')
        for sample_id, threshold in ((old_id,.08),(new_id,.4)):
            row = json.loads((original.folder/'samples'/sample_id/'sample.json').read_text())
            self.assertEqual(row['capture_policy']['max_skew_s'], threshold)
        self.assertEqual(original.manifest_path.read_bytes(), before)

    def test_eye_to_hand_transform_direction_and_degenerate_rejection(self):
        rng = np.random.default_rng(4)
        bc = transform([.4, -.3, .7], [.4, -.3, .8])
        gt = transform([.01, .02, .1], [.2, .1, -.2])
        bases = [transform(rng.uniform(-.3, .3, 3), rng.uniform(-1, 1, 3)) for _ in range(20)]
        cameras = [np.linalg.inv(bc) @ b @ gt for b in bases]
        actual_bc, actual_gt = fit_handeye(bases, cameras)
        np.testing.assert_allclose(actual_bc, bc, atol=1e-8)
        np.testing.assert_allclose(actual_gt, gt, atol=1e-8)
        bases = [transform([i*.01, 0, 0], [0, 0, i*.03]) for i in range(15)]
        with self.assertRaisesRegex(ValueError, "independent rotations"):
            fit_handeye(bases, [np.linalg.inv(bc) @ b @ gt for b in bases])

    def test_left_arm_capture_uses_left_feedback_and_separate_session(self):
        config = read_config(DEFAULT_CONFIG, arm='left')
        self.assertEqual(config['arm'], 'left')
        self.assertEqual(config['camera'], 'front')
        self.assertEqual(config['capture']['max_skew_s'], .4)
        dataset = Dataset(self.root / 'left', config, 'handeye', self.intrinsics)
        collector = Collector(dataset)
        left_q = [-.2, .7, -.7, -.1, .2, .1, .02]
        for index in range(7):
            reply = self.reply(10_000_000_000 + index*200_000_000)
            reply['positions']['left'] = left_q
            reply['joint_timestamps_ns']['left'] = reply['image_timestamps_ns']['front']
            # Invalid opposite-arm feedback must not affect left capture.
            reply['positions']['right'] = [float('nan')]*7
            reply['joint_timestamps_ns']['right'] = 1
            with patch('primitive_vla_cobotmagic.calibration.core.time.monotonic', return_value=index*.2):
                collector.ingest(reply, .001)
        with patch('primitive_vla_cobotmagic.calibration.core.time.monotonic', return_value=1.2):
            self.assertTrue(collector.state()['ready'], collector.message)
            self.assertEqual(collector.state()['arm'], 'left')
            sample_id = collector.record('train')
        saved = json.loads((dataset.folder/'samples'/sample_id/'sample.json').read_text())
        self.assertEqual(saved['arm'], 'left')
        self.assertEqual(saved['joints_rad'], left_q[:6])
        np.testing.assert_allclose(saved['T_base_from_tip'], dataset.chain.fk(np.array(left_q[:6])))
        with self.assertRaisesRegex(ValueError, 'changed'):
            Dataset(dataset.folder, self.config, 'handeye', self.intrinsics)
        # Missing selected-arm feedback must not silently use the other arm.
        with self.assertRaises(ValueError):
            collector.ingest(self.reply(12_000_000_000), .001)

    def test_real_detector_and_intrinsic_solver_use_held_out_samples(self):
        dataset = Dataset(self.root / "intrinsics", self.config, "intrinsics")
        rng = np.random.default_rng(10)
        attempts = 0
        while len(dataset.samples) < 22 and attempts < 100:
            attempts += 1
            ct = transform([rng.uniform(-.18, .05), rng.uniform(-.17, .02), rng.uniform(.5, .85)],
                           rng.uniform(-.5, .5, 3))
            jpeg = raster(self.board, ct, self.k)
            sample, _ = self.board.detect(jpeg)
            if len(sample['ids']) < 12: continue
            sample.update(image_timestamp_ns=attempts, joint_timestamp_ns=attempts,
                          joints_rad=[0]*6, camera="front", arm="right")
            dataset.save(sample, jpeg, "train" if len(dataset.samples) < 17 else "validation")
        result = solve_session(dataset.folder)
        self.assertTrue(result['accepted'], result)
        self.assertEqual(len(result['validation_ids']), 5)
        self.assertLess(abs(result['K'][0][0] - 900), 20)
        self.assertFalse(result['physical_accuracy_verified'])

    def test_full_handeye_solve_with_synthetic_urdf_poses_and_bad_validation(self):
        self.check_handeye_solve(self.config)

    def test_left_handeye_solve_reuses_front_intrinsics(self):
        self.check_handeye_solve(read_config(DEFAULT_CONFIG, arm='left'))

    def test_handeye_solve_keeps_measured_angles_outside_reference_limits(self):
        self.check_handeye_solve(self.config, outside_limits=True)

    def test_handeye_reevaluates_new_limits_without_changing_fit_or_manifest(self):
        config = deepcopy(self.config)
        config['quality'].update(max_validation_translation_m=.01, max_validation_rotation_deg=3.)
        self.check_handeye_solve(config, reevaluate=True)

    def test_capture_warns_about_reference_limits_without_clipping_feedback(self):
        dataset = Dataset(self.root / 'limit-warning', self.config, 'handeye', self.intrinsics)
        collector = Collector(dataset)
        for index in range(7):
            reply = self.reply(10_000_000_000 + index*200_000_000)
            reply['positions']['right'][3] = float(dataset.chain.upper[3] + np.deg2rad(.5))
            with patch('primitive_vla_cobotmagic.calibration.core.time.monotonic', return_value=index*.2):
                collector.ingest(reply, .001)
        with patch('primitive_vla_cobotmagic.calibration.core.time.monotonic', return_value=1.2):
            self.assertTrue(collector.state()['ready'], collector.message)
            self.assertIn('joint4', collector.message)
            sample_id = collector.record('train')
        row = json.loads((dataset.folder/'samples'/sample_id/'sample.json').read_text())
        self.assertEqual(row['joints_rad'][3], reply['positions']['right'][3])
        self.assertEqual(row['urdf_limit_exceedances'][0]['joint'], 'joint4')
        self.assertAlmostEqual(row['urdf_limit_exceedances'][0]['excess_deg'], .5)
        np.testing.assert_allclose(row['T_base_from_tip'], dataset.chain.fk(np.array(row['joints_rad'])))
        # Command validation remains strict; the exception is only for read-only measurement.
        with self.assertRaisesRegex(ValueError, 'limits'):
            dataset.chain.check(row['joints_rad'])

    def check_handeye_solve(self, config, outside_limits=False, reevaluate=False):
        dataset = Dataset(self.root / "handeye", config, "handeye", self.intrinsics)
        bc = transform([.15, 0, -.7], [0, 0, 0])
        gt = transform([-.08, -.1, .05], [0, -np.pi/2, 0])
        rng = np.random.default_rng(24)
        for index in range(22):
            q = np.array([rng.uniform(-.6,.6), rng.uniform(.3,1.2), rng.uniform(-1.2,-.2),
                          rng.uniform(-.5,.5), rng.uniform(-.4,.6), rng.uniform(-.6,.6)])
            if outside_limits and index in (0, 18):
                q[3] = dataset.chain.upper[3] + np.deg2rad(.5)
            bg = dataset.chain.fk(q)
            ct = np.linalg.inv(bc) @ bg @ gt
            objects = self.board.board.getChessboardCorners()
            pixels = cv2.projectPoints(objects, cv2.Rodrigues(ct[:3,:3])[0], ct[:3,3], self.k, np.zeros(5))[0]
            # Exact projected corners isolate hand-eye math from raster sampling noise.
            sample = {"ids": list(range(len(objects))), "corners": pixels.reshape(-1,2).tolist(),
                "image_size": [1280,720], "image_timestamp_ns": index+1, "joints_rad": q.tolist()}
            jpeg = raster(self.board, ct, self.k)
            dataset.save(sample, jpeg, "train" if index < 17 else "validation")
        result = solve_session(dataset.folder)
        self.assertTrue(result['accepted'], result)
        self.assertEqual(result['arm'], config['arm'])
        if outside_limits:
            self.assertEqual(len(result['urdf_limit_exceedances']), 2)
            self.assertTrue(result['warnings'])
        np.testing.assert_allclose(result['T_base_from_camera'], bc, atol=1e-4)
        if reevaluate:
            manifest_before = dataset.manifest_path.read_bytes()
            updated = solve_session(dataset.folder, acceptance_limits={
                'max_validation_translation_m':.02, 'max_validation_rotation_deg':5.})
            self.assertEqual(updated['thresholds']['max_validation_translation_m'], .02)
            self.assertEqual(updated['thresholds']['max_validation_rotation_deg'], 5.)
            self.assertEqual(updated['T_base_from_camera'], result['T_base_from_camera'])
            self.assertEqual(dataset.manifest_path.read_bytes(), manifest_before)
        before = result['T_base_from_camera']
        for sample_id in result['validation_ids']:
            path = dataset.folder / 'samples' / sample_id / 'sample.json'
            row = json.loads(path.read_text())
            row['joints_rad'][0] += .15
            write_json(path, row)
        wrong = solve_session(dataset.folder)
        self.assertFalse(wrong['accepted'])
        np.testing.assert_allclose(wrong['T_base_from_camera'], before, atol=1e-10)

    def test_web_recording_and_offline_state_without_robot_requests(self):
        collector = Collector(Dataset(self.root / 'web', self.config, 'intrinsics'))
        async def check():
            # ASGITransport does not start lifespan or the network poll loop.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(collector)), base_url="http://test") as client:
                self.assertFalse((await client.get('/api/state')).json()['ready'])
                self.assertEqual((await client.post('/api/record', json={"split":"train"})).status_code, 409)
                collector.ingest(self.reply(), .001)
                self.assertEqual((await client.post('/api/record', json={"split":"validation"})).status_code, 200)
                self.assertEqual((await client.get('/api/state')).json()['counts']['validation'], 1)
                self.assertEqual((await client.post('/api/record', json={"split":"bad"})).status_code, 422)
                self.assertEqual((await client.post('/api/record', json={"split":"train"}, headers={"Origin":"https://evil.test"})).status_code,403)
                self.assertIn("keydown", (await client.get('/')).text)
        asyncio.run(check())

    def test_disconnect_invalidates_previously_ready_frame(self):
        collector = Collector(Dataset(self.root / 'disconnect', self.config, 'intrinsics'))
        collector.ingest(self.reply(), .001)
        collector.bridge.observation = AsyncMock(side_effect=ConnectionError("offline"))
        async def check():
            task = asyncio.create_task(collector.poll())
            await asyncio.sleep(.01)
            self.assertIsNone(collector.latest)
            self.assertFalse(collector.state()['ready'])
            self.assertIn('offline', collector.state()['message'])
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        asyncio.run(check())

    def test_failed_solve_invalidates_old_result(self):
        dataset = Dataset(self.root / 'failure', self.config, 'intrinsics')
        write_json(dataset.folder / 'result.json', {'accepted': True})
        with self.assertRaisesRegex(ValueError, 'training'):
            solve_session(dataset.folder)
        self.assertFalse(json.loads((dataset.folder / 'result.json').read_text())['accepted'])


if __name__ == '__main__':
    unittest.main()
