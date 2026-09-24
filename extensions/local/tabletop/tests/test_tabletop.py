"""Contracts runnable without torch, SAPIEN, model weights or credentials."""

import importlib.util
from pathlib import Path
import tempfile
from time import time
import unittest
from unittest.mock import AsyncMock

from primitive_vla.config.loader import load_config
from primitive_vla.storage.events import EventLog
from primitive_vla_tabletop.provider import TabletopTools
from primitive_vla.tools import PluginContext, ToolCall
from primitive_vla.tools.runtime import ToolRuntime


CONFIG = Path(__file__).resolve().parents[1] / "configs/tabletop.yaml"


class TabletopContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_dispatches_image_refs_and_bounded_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = TabletopTools(load_config(CONFIG).tool_providers[0].options,
                                    PluginContext(Path(tmp), CONFIG.parent, EventLog()))
            provider.process.request = AsyncMock(return_value={"observation_id": "view1", "images": {"top": "frame.png"}})
            runtime = ToolRuntime(provider.tools)
            result = await runtime.invoke(ToolCall(name="tabletop.observe"))
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.images, {"top": "frame.png"})
            self.assertEqual(result.data, {"observation_id": "view1"})
            request = provider.process.request.call_args
            self.assertEqual(request.args, ("observe",))
            self.assertLess(request.kwargs["deadline"], time() + 181)
            self.assertGreater(request.kwargs["deadline"], time())
            self.assertTrue(Path(provider.process.init_payload["options"]["models_dir"]).is_absolute())
            await runtime.close()
            await provider.close()

    async def test_unacknowledged_robot_stop_locks_further_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = TabletopTools(load_config(CONFIG).tool_providers[0].options,
                                    PluginContext(Path(tmp), CONFIG.parent, EventLog()))
            operations = []
            async def request(op, **kwargs):
                operations.append(op)
                if op == "stop":
                    return {"stopped": True, "call_id": "different-call"}
                raise RuntimeError("Worker connection failed")
            provider.process.request = request
            runtime = ToolRuntime(provider.tools)
            failed = await runtime.invoke(ToolCall(name="robot.gripper", arguments={"width_m": .08}))
            self.assertEqual(failed.status, "error")
            self.assertFalse(failed.control_released)
            rejected = await runtime.invoke(ToolCall(name="robot.gripper", arguments={"width_m": 0.}))
            self.assertEqual(rejected.status, "rejected")
            self.assertEqual(operations, ["gripper", "stop"])
            await runtime.close()
            await provider.close()


@unittest.skipUnless(importlib.util.find_spec("numpy"), "Optional geometry dependency: numpy")
class CalibrationTests(unittest.TestCase):
    def test_camera_projection_round_trip_in_world_frame(self):
        import numpy as np
        from primitive_vla_tabletop.geometry import unproject
        intrinsics = np.array([[500, 0, 320], [0, 500, 240], [0, 0, 1.]])
        world_from_camera = np.array([[1,0,0,.4], [0,-1,0,.1], [0,0,-1,.8], [0,0,0,1.]])
        world_point = np.array([.46, .07, .05])
        camera_point = (np.linalg.inv(world_from_camera) @ np.r_[world_point, 1])[:3]
        pixel = intrinsics @ camera_point
        actual = unproject(pixel[:2] / pixel[2], camera_point[2], intrinsics, world_from_camera)
        np.testing.assert_allclose(actual, world_point, atol=1e-8)

    def test_invalid_depth_and_off_table_grasps_are_rejected(self):
        import numpy as np
        from primitive_vla_tabletop.geometry import unproject, grasp_pose
        for depth in (float("nan"), float("inf"), 0, -1):
            with self.assertRaises(ValueError):
                unproject([320, 240], depth, np.eye(3), np.eye(4))
        intrinsics = np.array([[500,0,320],[0,500,240],[0,0,1.]])
        with self.assertRaisesRegex(ValueError, "height"):
            grasp_pose([320,240], 0, 30, .75, intrinsics, np.eye(4))

    def test_grasp_rotation_is_right_handed_and_uses_metres(self):
        import numpy as np
        from primitive_vla_tabletop.geometry import grasp_pose
        intrinsics = np.array([[500,0,320],[0,500,240],[0,0,1.]])
        extrinsics = np.diag([1.,-1.,-1.,1.])
        extrinsics[:3,3] = [.45, .02, .8]
        center, rotation, width = grasp_pose([320,240], .3, 40, .75, intrinsics, extrinsics)
        np.testing.assert_allclose(center, [.45,.02,.032])
        self.assertAlmostEqual(width, .06)
        self.assertAlmostEqual(np.linalg.det(rotation), 1.)
        np.testing.assert_allclose(rotation.T @ rotation, np.eye(3), atol=1e-8)
        np.testing.assert_allclose(rotation[:,2], [0,0,-1])


if __name__ == "__main__":
    unittest.main()
