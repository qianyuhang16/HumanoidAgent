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
