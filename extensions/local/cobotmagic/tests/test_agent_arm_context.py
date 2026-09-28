"""Arm-choice evidence uses calibration, not fixed image halves; no hardware."""
import json
import unittest
import numpy as np
import test_agent


class ArmContextTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_agent.AgentToolsTests.asyncSetUp
    call = test_agent.AgentToolsTests.call

    def geometry(self, swapped=False):
        for arm, offset in [('left', .30), ('right', -.30)]:
            folder, result, manifest = self.provider.ik.calibration_result(arm, 'handeye')
            t = np.eye(4)
            t[:3,:3] = np.diag([1., -1., -1.])
            t[:3,3] = [offset*(-1 if swapped else 1), 0., 1.]
            result['T_base_from_camera'] = t.tolist()
            (folder/'result.json').write_text(json.dumps(result))

    async def detect(self, pixel):
        obs = await self.provider.observe({}, self.call())
        self.inference.pixel = pixel
        return await self.provider.segment({'observation_id':obs.data['observation_id'],
                                             'query':'pink bowl'}, self.call())

    async def test_left_right_and_central_targets_get_comparable_arm_evidence(self):
        self.geometry()
        for pixel, preferred in [([150,240], 'left'), ([490,240], 'right'), ([320,240], None)]:
            result = await self.detect(pixel)
            evidence = result.data['arm_context']
            self.assertEqual(evidence['preferred_arm'], preferred)
            self.assertEqual(set(evidence['candidates']), {'left','right'})
            self.assertFalse(evidence['reachability_checked'])

    async def test_calibration_changes_preference_even_with_same_pixel(self):
        self.geometry(swapped=True)
        result = await self.detect([150,240])
        self.assertEqual(result.data['arm_context']['preferred_arm'], 'right')

    async def test_missing_calibration_does_not_invent_arm_preference(self):
        self.geometry()
        folder, _, _ = self.provider.ik.calibration_result('right','handeye')
        (folder/'result.json').unlink()
        result = await self.detect([150,240])
        self.assertTrue(result.data['found'])
        context = result.data['arm_context']
        self.assertIsNone(context['preferred_arm'])
        self.assertIn('error', context['candidates']['right'])

    async def test_agent_plan_result_keeps_decision_fields_without_dense_plot_coordinates(self):
        result = await self.detect([320,240])
        plan = await self.provider.plan({'detection_id':result.data['detection_id'], 'arm':'left'},self.call())
        self.assertTrue(plan.data['execution_ready'], plan.data)
        self.assertIn('plan_id',plan.data)
        self.assertIn('arm_context',plan.data)
        self.assertNotIn('path_pixels',plan.data)
        self.assertIn('path_pixels',self.provider.last_plan)


if __name__ == '__main__':
    unittest.main()
