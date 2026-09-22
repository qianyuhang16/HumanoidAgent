"""Training layout differs from both simulator and Mem-0: guard arm/gripper identity."""
import unittest
import numpy as np
from primitive_vla.vla.pi05_rmbench import encode_state, decode_actions


class Pi05MappingTests(unittest.TestCase):
    def test_training_layout_preserves_arm_and_gripper_identity(self):
        robot = np.array([10,11,12,13,14,15,.2,20,21,22,23,24,25,.8], dtype=np.float32)
        training = encode_state(robot)
        np.testing.assert_array_equal(training, np.array(
            [10,11,12,13,14,15,0,20,21,22,23,24,25,0,.2,.8], dtype=np.float32))
        np.testing.assert_array_equal(decode_actions(training[None])[0], robot)
