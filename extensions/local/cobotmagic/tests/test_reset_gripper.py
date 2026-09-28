"""Reset arm/gripper together using only a fake bridge and clock."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from primitive_vla_cobotmagic.agent.reset import ResetRun
from primitive_vla_cobotmagic.ik.execution import MotionBridge, BRIDGE_LIMITS
from primitive_vla_cobotmagic.vla.config import ResetSettings
from test_hover import Clock, FakeBridge, snapshot


class GripperResetTests(unittest.TestCase):
    def setUp(self):
        self.positions={'left':[.1,.3,-.2,0.,0.,0.,.04],
                        'right':[.2,.4,-.3,0.,0.,0.,.02]}
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)

    def run_fake(self, arm='left', settings=None, bridge=None):
        settings=settings or ResetSettings()
        clock=Clock()
        bridge=bridge or FakeBridge(self.positions)
        bridge.close=lambda:None
        run=ResetRun({'feedback_limit_tolerance_deg':2.},settings.model_dump(),arm,Path(self.temp.name)/arm)
        run.cancelled=SimpleNamespace(is_set=lambda:False,wait=clock.sleep)
        self.run=run
        with patch('primitive_vla_cobotmagic.agent.reset.MotionBridge',return_value=bridge), \
             patch('primitive_vla_cobotmagic.agent.reset.time',SimpleNamespace(monotonic=clock.monotonic)):
            result=run.run()
        return result,bridge,clock

    def test_each_arm_uses_its_own_gripper_target_and_preserves_the_other_arm(self):
        settings=ResetSettings(gripper_position_m={'left':0.,'right':.065})
        for arm in ('left','right'):
            with self.subTest(arm=arm):
                result,bridge,clock=self.run_fake(arm,settings)
                goal=settings.gripper_position_m[arm]
                other='right' if arm=='left' else 'left'
                self.assertTrue(result['arrival_verified'])
                self.assertEqual(result['target_gripper_m'],goal)
                self.assertAlmostEqual(result['gripper_error_m'],0)
                self.assertGreaterEqual(clock.now,3)
                self.assertAlmostEqual(bridge.commands[-1][arm][6],goal)
                for command in bridge.commands:
                    self.assertEqual(command[other],self.positions[other])
                    self.assertTrue(min(goal,self.positions[arm][6])<=command[arm][6]<=max(goal,self.positions[arm][6]))

    def test_five_degree_joint_and_five_mm_gripper_tolerances_use_feedback(self):
        class ApproximateBridge(FakeBridge):
            def action(inner,positions):
                super().action(positions)
                inner.positions['left'][0]+=.06
                inner.positions['left'][6]+=.004
        result,_,_=self.run_fake(bridge=ApproximateBridge(self.positions))
        self.assertAlmostEqual(result['joint_error_rad'],.06)
        self.assertAlmostEqual(result['gripper_error_m'],.004)
        self.assertTrue(result['arrival_verified'])

    def test_closed_feedback_slightly_below_zero_is_not_rewritten_or_rejected(self):
        class NegativeZeroBridge(FakeBridge):
            def action(inner,positions):
                super().action(positions)
                inner.positions['left'][6]-=.0003
        result,_,_=self.run_fake(bridge=NegativeZeroBridge(self.positions))
        self.assertAlmostEqual(result['positions']['left'][6],-.0003)
        self.assertAlmostEqual(result['gripper_error_m'],.0003)

    def test_joint_arrival_alone_cannot_claim_a_stuck_gripper_reset_succeeded(self):
        class StuckGripper(FakeBridge):
            def action(inner,positions):
                super().action(positions)
                inner.positions['left'][6]=.04
        bridge=StuckGripper(self.positions)
        with self.assertRaisesRegex(TimeoutError,'gripper error'):
            self.run_fake(settings=ResetSettings(settle_timeout_s=.05),bridge=bridge)
        self.assertFalse(self.run.completed)
        self.assertFalse(self.run.control_released)
        self.assertTrue(any(command['left'][6]==0 for command in bridge.commands))
        self.assertEqual(bridge.commands[-1]['left'][6],.04)  # Failure holds current feedback.

    def test_reset_only_feedback_tolerance_accepts_four_degree_boundary_error(self):
        connection={'arm':'right','feedback_limit_tolerance_deg':2.,'max_feedback_age_s':.2,'request_timeout_s':.2}
        original=deepcopy(connection)
        settings=ResetSettings()
        run=ResetRun(connection,settings.model_dump(),'right',Path(self.temp.name)/'bounds')
        bridge=MotionBridge(run.config)
        raw=deepcopy(self.positions)
        raw['right'][4]=float(BRIDGE_LIMITS[4,0]-np.deg2rad(4))
        bridge.request=lambda _:snapshot(raw)
        measured,_=bridge.observation()
        self.assertEqual(measured['right'][4],raw['right'][4])
        self.assertEqual(connection,original)
        bridge=MotionBridge(connection)
        bridge.request=lambda _:snapshot(raw)
        with self.assertRaises(ValueError):bridge.observation()

    def test_invalid_gripper_targets_are_rejected_before_connecting(self):
        for target in (-.001,.081,float('nan'),float('inf')):
            with self.subTest(target=target),self.assertRaises(ValueError):
                ResetSettings(gripper_position_m={'left':target,'right':0.})
