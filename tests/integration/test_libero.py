"""LIBERO skill handoff preserves the world and rejects revoked policy output."""

import asyncio
import os
from pathlib import Path
import tempfile
from time import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from primitive_vla.backends.libero import LiberoBackend
from primitive_vla.config.loader import load_config
from primitive_vla.domain.models import ExecutionStatus, SkillCall
from primitive_vla.domain.skills import Registry
from primitive_vla.execution.control import ControlAuthority
from primitive_vla.storage.events import EventLog


def config():
    return load_config(Path(__file__).resolve().parents[2]/'configs/libero-agent.json')


class LiberoTests(unittest.IsolatedAsyncioTestCase):
    async def test_revoked_inference_never_reaches_simulator(self):
        cfg = config()
        with tempfile.TemporaryDirectory() as directory:
            backend = LiberoBackend(cfg.backends['libero'].libero, Path(directory), EventLog())
            backend.ready = True
            entered, finish = asyncio.Event(), asyncio.Event()

            async def infer(*args, **kwargs):
                entered.set()
                await finish.wait()
                return {'actions': [[0.]*7]*10}

            backend.policy = SimpleNamespace(request=infer)
            backend.world = SimpleNamespace(request=AsyncMock(return_value={'path': 'image.npz'}))
            call = SkillCall('TRANSFER', {'object': 'alphabet_soup', 'destination': 'basket'}, 0)
            candidate = Registry(cfg.skills).resolve(call, {o.id:o for o in cfg.scene.objects})
            control = ControlAuthority()
            lease = control.acquire(call, candidate)
            running = asyncio.create_task(backend.run(call, candidate, lease, control, lambda _:None))
            await entered.wait()
            control.revoke()
            finish.set()
            self.assertEqual(await running, ExecutionStatus.CANCELLED)
            self.assertEqual([call.args[0] for call in backend.world.request.call_args_list],
                             ['begin', 'model_observation'])

    @unittest.skipUnless(os.environ.get('RUN_LIBERO_TESTS') == '1', 'requires local LIBERO and rendering GPU')
    async def test_world_survives_handoff_and_stop_rejects_late_chunk(self):
        cfg = config()
        with tempfile.TemporaryDirectory() as directory:
            backend = LiberoBackend(cfg.backends['libero'].libero, Path(directory), EventLog())
            try:
                await backend.observe()
                for generation in (1, 2):
                    call_id = f'call-{generation}'
                    await backend.world.request('begin', call_id=call_id, generation=generation,
                        deadline=time()+30, effects={'benchmark:success': 'true'})
                    await backend.world.request('chunk', call_id=call_id, generation=generation,
                        chunk=1, actions=[[0.]*6+[-1.]]*2)
                    await backend.stop(call_id, 'handoff')
                    with self.assertRaisesRegex(RuntimeError, 'No matching active skill lease'):
                        await backend.world.request('chunk', call_id=call_id, generation=generation,
                            chunk=2, actions=[[0.]*7])
                state = (await backend.observe()).proprioception
                self.assertEqual(state['control_steps'], 4)
                self.assertEqual(state['reset_count'], 1)
            finally:
                await backend.close()
