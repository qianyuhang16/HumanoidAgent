"""Reproduce the recorded reset/observation failures with fake hardware only."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import yaml

from primitive_vla.tools import CallContext, ToolCall
from primitive_vla.tools.runtime import ToolRuntime
from primitive_vla_cobotmagic.agent.reset import ResetRun
from primitive_vla_cobotmagic.ik.execution import current_state_command, BRIDGE_LIMITS, MotionBridge
from primitive_vla_cobotmagic.vla.config import ResetSettings
import test_agent
from test_hover import Clock, snapshot


class ResetObservationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_agent.AgentToolsTests.asyncSetUp
    call = test_agent.AgentToolsTests.call
    detect = test_agent.AgentToolsTests.detect

    async def test_recorded_wrist_feedback_allows_either_arm_reset_with_deployed_config(self):
        config = yaml.safe_load((test_agent.ROOT/'configs/ik.yaml').read_text())['motion']
        original = deepcopy(self.camera.raw['positions'])
        original['right'][4] = float(np.deg2rad(-71.170))
        expected_hold = current_state_command(original, feedback_limit_tolerance_deg=2.)
        for arm in ('left', 'right'):
            with self.subTest(arm=arm):
                clock = Clock()
                bridge = MotionBridge({**config,'arm':arm}, allow_motion=True)
                positions = deepcopy(original)
                commands = []
                def request(body):
                    if body['op']=='get_observation':
                        return snapshot(deepcopy(positions),1_000_000_000+len(commands)*50_000_000)
                    self.assertEqual(body['op'],'send_action')
                    command=deepcopy(body['positions'])
                    commands.append(command)
                    positions.update(command)
                    return {'ok':True,'positions':command}
                bridge.request=request  # Actual decoding/action validation, no socket.
                run = ResetRun(config, ResetSettings().model_dump(), arm, self.root/('reset-'+arm))
                run.cancelled = SimpleNamespace(is_set=lambda: False, wait=clock.sleep)
                with patch('primitive_vla_cobotmagic.agent.reset.MotionBridge', return_value=bridge), \
                     patch('primitive_vla_cobotmagic.agent.reset.time', SimpleNamespace(monotonic=clock.monotonic)):
                    result = run.run()
                self.assertTrue(result['arrival_verified'])
                self.assertEqual(commands[-1][arm][:6], [0.]*6)
                self.assertEqual(commands[-1][arm][6],0.)
                other = 'right' if arm == 'left' else 'left'
                for command in commands:
                    self.assertEqual(command[other], expected_hold[other])
                    self.assertTrue(0<=command[arm][6]<=expected_hold[arm][6])
                    self.assertTrue(np.all(np.asarray(command[arm][:6]) >= BRIDGE_LIMITS[:, 0]))
                    self.assertTrue(np.all(np.asarray(command[arm][:6]) <= BRIDGE_LIMITS[:, 1]))

    async def test_reset_rejected_before_send_keeps_image_and_detections(self):
        await self.detect()
        observation = self.provider.observation['observation_id']
        before = deepcopy(self.provider.detections)
        self.provider.arm_state.used(['left'])
        with patch('primitive_vla_cobotmagic.agent.reset.ResetRun.run', side_effect=ValueError('feedback rejected')):
            with self.assertRaisesRegex(ValueError, 'feedback rejected'):
                await self.provider.reset_arm({'arm':'right'}, self.call())
        self.assertEqual(self.provider.current(observation)['observation_id'], observation)
        self.assertEqual(self.provider.detections, before)
        self.assertEqual(self.provider.arm_state.dirty(), {'left'})
        result = await self.provider.segment({'observation_id':observation, 'query':'blue cup'}, self.call())
        self.assertTrue(result.data['found'])

    async def test_observe_returns_only_camera_and_image_handle(self):
        result = await self.provider.observe({}, self.call())
        self.assertEqual(set(result.data), {'observation_id', 'camera'})
        self.assertEqual(result.data['camera'], {'name':'front', 'placement':'fixed_front'})
        self.assertEqual(set(result.images), {'front'})

    async def test_segment_defaults_to_current_image_through_real_tool_schema(self):
        await self.provider.observe({}, self.call())
        runtime = ToolRuntime(self.provider.tools)
        try:
            result = await runtime.invoke(ToolCall(name='perception.segment', arguments={'query':'blue cup'}))
            self.assertEqual(result.status, 'ok', result.text)
            self.assertTrue(result.data['found'])
        finally:
            await runtime.close()

    async def test_current_capture_call_id_is_a_valid_image_reference(self):
        call = self.call()
        await self.provider.observe({}, call)
        result = await self.provider.segment({'observation_id':call.call_id, 'query':'blue cup'}, self.call())
        self.assertTrue(result.data['found'])

    async def test_actual_partial_motion_discards_image_and_keeps_control_uncertainty(self):
        await self.detect()
        def fail_after_send(run):
            run.sent=True
            raise RuntimeError('connection lost after send')
        runtime=ToolRuntime(self.provider.tools)
        try:
            with patch('primitive_vla_cobotmagic.agent.reset.ResetRun.run',fail_after_send):
                result=await runtime.invoke(ToolCall(name='robot.reset_arm',arguments={'arm':'left'}))
            self.assertEqual(result.status,'error')
            self.assertIsNone(self.provider.observation)
            self.assertEqual(self.provider.detections,{})
            self.assertEqual(self.provider.arm_state.dirty(),{'left'})
            self.assertTrue(runtime.control_locked)
        finally:
            await runtime.close()

    async def test_failed_execution_keeps_image_but_does_not_reuse_the_consumed_plan(self):
        detection=await self.detect()
        plan=(await self.provider.plan({'detection_id':detection.data['detection_id'],'arm':'left'},self.call())).data
        observation=self.provider.observation['observation_id']
        with patch('primitive_vla_cobotmagic.agent.motion.MotionRun.run',side_effect=ValueError('start changed')):
            with self.assertRaises(ValueError):
                await self.provider.execute({'plan_id':plan['plan_id']},self.call())
        self.assertEqual(self.provider.current(observation)['observation_id'],observation)
        self.assertIsNone(self.provider.last_plan)
        self.assertEqual(self.provider.arm_state.dirty(),set())
        with self.assertRaisesRegex(ValueError,'Unknown or superseded plan_id'):
            await self.provider.execute({'plan_id':plan['plan_id']},self.call())

    async def test_omitted_reference_captures_when_missing_but_explicit_old_reference_stays_invalid(self):
        call=CallContext('capture-one',self.call().deadline)
        old=await self.provider.observe({},call)
        self.provider.invalidate()
        for token in (old.data['observation_id'],call.call_id):
            with self.assertRaises(ValueError):
                await self.provider.segment({'observation_id':token,'query':'blue cup'},self.call())
        self.inference.found=False
        result=await self.provider.segment({'query':'blue cup'},self.call())
        self.assertFalse(result.data['found'])
        self.assertIn('front',result.images)
        self.assertEqual(self.camera.calls,2)
        self.assertNotEqual(result.data['observation_id'],old.data['observation_id'])

    async def test_state_does_not_expose_internal_motion_configuration_or_joint_vectors(self):
        self.provider.preparations={'ready':{'arm':'left','joints_rad':[0.]*6}}
        result=await self.provider.state({},self.call())
        self.assertNotIn('reset',result.data)
        self.assertEqual(result.data['preparations'],{'ready':{'arm':'left'}})
