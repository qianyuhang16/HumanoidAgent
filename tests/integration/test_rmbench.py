"""RMBench must discard model output after a control lease is revoked."""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from primitive_vla.backends.rmbench import RMBenchBackend
from primitive_vla.domain.models import ExecutionStatus, SceneObject, SkillCall
from primitive_vla.domain.skills import Registry
from primitive_vla.execution.control import ControlAuthority
from primitive_vla.skills.loader import load_skills
from primitive_vla.storage.events import EventLog


class RMBenchTests(unittest.IsolatedAsyncioTestCase):
    async def test_revoked_prediction_is_not_dispatched(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = RMBenchBackend({'python': 'unused', 'mode': 'vla'}, Path(directory), EventLog())
            backend.ready = True
            entered, finish = asyncio.Event(), asyncio.Event()

            async def request(operation, **kwargs):
                if operation == 'infer':
                    entered.set()
                    await finish.wait()
                    return {'actions': [[0.] * 14], 'elapsed_s': 0.1}
                return {'started': True}

            backend.worker.request = AsyncMock(side_effect=request)
            call = SkillCall('COVER', {'position': 'left'}, 0)
            candidate = Registry(load_skills(['builtin:rmbench'])).resolve(
                call, {'left': SceneObject('left', 'cover_position', 'left')})
            authority = ControlAuthority()
            lease = authority.acquire(call, candidate)
            running = asyncio.create_task(backend.run(call, candidate, lease, authority, lambda _: None))
            await entered.wait()
            authority.revoke()
            finish.set()
            self.assertEqual(await running, ExecutionStatus.CANCELLED)
            self.assertEqual([c.args[0] for c in backend.worker.request.call_args_list], ['begin', 'infer'])

    async def test_false_end_signal_requires_agent_recovery_and_physical_effect(self):
        from primitive_vla.domain.models import Observation, Fact, Truth
        with tempfile.TemporaryDirectory() as directory:
            supervisor = AsyncMock(return_value={'action': 'continue', 'reason': 'Cover still moving'})
            backend = RMBenchBackend({'python': 'unused', 'mode': 'ik_approach'},
                                     Path(directory), EventLog(), supervisor)
            backend.ready = True
            backend.observe = AsyncMock(side_effect=[
                Observation({'covered:left': Fact(Truth.FALSE, 'test')}),
                Observation({'covered:left': Fact(Truth.TRUE, 'test')})])
            count = 0

            async def request(operation, **kwargs):
                nonlocal count
                if operation == 'infer':
                    return {'actions': [[0.] * 14], 'elapsed_s': 0.1}
                if operation == 'chunk':
                    count += 1
                    return dict(skill_steps=count, end_signals=2, fail_flag=False,
                                subtask_end=True, success=False, exhausted=False)
                if operation == 'stop':
                    return {'call_id': kwargs['call_id'], 'stopped': True}
                return {}

            backend.worker.request = AsyncMock(side_effect=request)
            call = SkillCall('COVER', {'position': 'left'}, 0)
            candidate = Registry(load_skills(['builtin:rmbench'])).resolve(
                call, {'left': SceneObject('left', 'cover_position', 'left')})
            authority = ControlAuthority()
            lease = authority.acquire(call, candidate)
            result = await backend.run(call, candidate, lease, authority, lambda _: None)
            self.assertEqual(result, ExecutionStatus.COMPLETED)
            supervisor.assert_awaited_once()
            operations = [c.args[0] for c in backend.worker.request.call_args_list]
            self.assertEqual(operations, ['begin', 'infer', 'chunk', 'resume', 'infer', 'chunk', 'stop'])

    async def test_revocation_during_agent_review_blocks_reapproach(self):
        from primitive_vla.domain.models import Observation, Fact, Truth
        with tempfile.TemporaryDirectory() as directory:
            authority = ControlAuthority()

            async def supervise(context, images):
                authority.revoke()
                return {'action': 'reapproach'}

            backend = RMBenchBackend({'python': 'unused', 'mode': 'ik_approach'},
                                     Path(directory), EventLog(), supervise)
            backend.ready = True
            backend.observe = AsyncMock(return_value=Observation({'covered:left': Fact(Truth.FALSE, 'test')}))

            async def request(operation, **kwargs):
                if operation == 'infer':
                    return {'actions': [[0.] * 14]}
                if operation == 'chunk':
                    return dict(skill_steps=1, end_signals=2, fail_flag=False,
                                subtask_end=True, success=False, exhausted=False)
                return {}

            backend.worker.request = AsyncMock(side_effect=request)
            call = SkillCall('COVER', {'position': 'left'}, 0)
            candidate = Registry(load_skills(['builtin:rmbench'])).resolve(
                call, {'left': SceneObject('left', 'cover_position', 'left')})
            lease = authority.acquire(call, candidate)
            result = await backend.run(call, candidate, lease, authority, lambda _: None)
            self.assertEqual(result, ExecutionStatus.CANCELLED)
            self.assertNotIn('resume', [c.args[0] for c in backend.worker.request.call_args_list])
