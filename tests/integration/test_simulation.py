"""Opt-in physical cancellation test using the configured RoboTwin installation."""

import asyncio
import os
from pathlib import Path
import tempfile
import unittest

from primitive_vla.config.loader import load_config
from primitive_vla.domain.models import EffectStatus, ExecutionStatus, SkillCall, Truth
from primitive_vla.runtime.session import Runtime
from primitive_vla.storage.events import EventLog
from primitive_vla.skills.loader import load_skills
from primitive_vla.backends.sapien import SapienBackend


@unittest.skipUnless(os.environ.get('RUN_SAPIEN_TESTS') == '1', 'requires RoboTwin/SAPIEN and a rendering GPU')
class SimulationTests(unittest.IsolatedAsyncioTestCase):
    async def test_policy_stop_rejects_late_chunks(self):
        config = load_config(Path(__file__).resolve().parents[2] / 'configs/sapien.json')
        config.backends['sapien'].simulation.record_video = False
        with tempfile.TemporaryDirectory(prefix='primitive-policy-cancel-') as directory:
            backend = SapienBackend(config.backends['sapien'].simulation, Path(directory))
            try:
                observation = await backend.observe()
                qpos = observation.proprioception['qpos']
                call = SkillCall('GRASP', {'object': 'red', 'source': 'table', 'hand': 'right'}, 0)
                from dataclasses import asdict
                await backend.request('policy_begin', call=asdict(call), generation=1)
                payload = dict(call_id=call.call_id, generation=1, chunk=1, actions=[qpos[:7] + [0.]] * 50,
                               action_hz=1, max_joint_step=.06, max_gripper_step=.01)
                pending = asyncio.create_task(backend.request('policy_chunk', **payload))
                await asyncio.sleep(.1)
                self.assertTrue(await backend.stop(call.call_id, 'test cancellation'))
                self.assertEqual((await pending)['status'], 'cancelled')
                with self.assertRaisesRegex(RuntimeError, 'matching active policy'):
                    await backend.request('policy_chunk', **{**payload, 'chunk': 2})
                before = await backend.observe()
                await asyncio.sleep(.1)
                after = await backend.observe()
                delta = sum((a-b)**2 for a,b in zip(before.proprioception['tcp'], after.proprioception['tcp']))**.5
                self.assertLess(delta, .025)
            finally:
                await backend.close()

    async def test_cancel_transfer_stops_arm_and_preserves_physical_grasp(self):
        root = Path(__file__).resolve().parents[2]
        config = load_config(root / 'configs/sapien.json')
        # This regression checks the historical scripted controller, independent
        # of the user's selected policy and learned grasp success.
        config.vla = None
        config.skill_sources = ['builtin:sapien']
        config.skills = load_skills(config.skill_sources)
        config.backends['sapien'].simulation.record_video = False
        config.backends['sapien'].simulation.realtime_factor = 3
        with tempfile.TemporaryDirectory(prefix='primitive-sapien-cancel-') as directory:
            runtime = Runtime(config, Path(directory), EventLog())
            try:
                runtime.state.update(await runtime.observer.observe())
                obj = config.backends['sapien'].simulation.objects[0].id
                grasp = SkillCall('GRASP', {'object': obj, 'source': 'table', 'hand': 'right'}, runtime.state.version)
                result = await runtime.executive.execute(grasp)
                self.assertEqual(result.effect, EffectStatus.SUCCESS)
                placement = SkillCall('PLACE', {'object': obj, 'target': 'bowl', 'hand': 'right'}, runtime.state.version)
                running = asyncio.create_task(runtime.executive.execute(placement))
                for _ in range(200):
                    if any(e['event'] == 'feedback' and e.get('phase') == f'transfer:{obj}' for e in runtime.log.events):
                        break
                    await asyncio.sleep(.02)
                else:
                    self.fail('Transfer never started')
                runtime.executive.cancel(placement.call_id)
                cancelled = await asyncio.wait_for(running, timeout=8)
                self.assertEqual(cancelled.execution, ExecutionStatus.CANCELLED)
                self.assertIsNone(runtime.executive.control.lease)
                self.assertTrue(any(e['event'] == 'stopped' and e['acknowledged'] for e in runtime.log.events))
                before = await runtime.observer.observe()
                await asyncio.sleep(.3)
                after = await runtime.observer.observe()
                self.assertEqual(after.facts[f'holding:right:{obj}'].value, Truth.TRUE)
                self.assertEqual(after.facts[f'at:{obj}:bowl'].value, Truth.FALSE)
                distance = sum((a - b) ** 2 for a, b in zip(before.proprioception['tcp'], after.proprioception['tcp'])) ** .5
                self.assertLess(distance, .025)
            finally:
                await runtime.close()
                self.assertIsNotNone(runtime.observer.process.returncode)
