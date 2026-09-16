"""Controller separation, bounded joint execution and late policy result rejection."""

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from primitive_vla.agent.planner import GoalPlanner
from primitive_vla.backends.sapien import SapienBackend
from primitive_vla.config.loader import load_config
from primitive_vla.domain.models import Fact, Goal, Observation, SkillCall, Task, Truth, ExecutionStatus
from primitive_vla.domain.skills import Registry
from primitive_vla.domain.state import State
from primitive_vla.execution.control import ControlAuthority
from primitive_vla.storage.events import EventLog

try:
    import numpy as np
    from primitive_vla.simulation.policy_control import JointPolicyControl
except ImportError:
    np = None


def configuration():
    return load_config(Path(__file__).resolve().parents[2] / 'configs/sapien.json')


class VLABoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_grasp_requires_move_to_then_policy_skill(self):
        cfg = configuration()
        state = State({o.id: o for o in cfg.scene.objects})
        state.update(Observation({key: Fact(value, 'test') for key, value in cfg.scene.facts.items()}))
        candidates = Registry(cfg.skills).candidates(state.objects)
        task = Task('Pick red', [Goal('held', {'holding:right:red': Truth.TRUE})])
        decision = await GoalPlanner().decide(task, state, candidates, [])
        self.assertEqual(decision.candidate.spec.name, 'MOVE_TO')
        self.assertEqual(decision.candidate.bindings['target'], 'red')
        state.update(Observation({'near:red': Fact(Truth.TRUE, 'test')}))
        decision = await GoalPlanner().decide(task, state, candidates, [])
        self.assertEqual(decision.candidate.spec.name, 'GRASP')
        self.assertTrue(decision.candidate.spec.policy_prompt)
        self.assertEqual(decision.candidate.spec.program, [])

    async def test_late_inference_cannot_dispatch_after_revocation(self):
        cfg = configuration()
        backend = SapienBackend(cfg.backends['sapien'].simulation, Path('/tmp'), cfg.vla, EventLog())
        entered, finish = asyncio.Event(), asyncio.Event()

        async def infer(*args):
            entered.set()
            await finish.wait()
            return {'actions': [[0.] * 8]}

        backend.policy = SimpleNamespace(start=AsyncMock(), infer=infer)
        backend.observe = AsyncMock(return_value=Observation({}, proprioception={'qpos': [0.] * 9}))
        backend.request = AsyncMock(return_value={'ready': True})
        call = SkillCall('GRASP', {'object': 'red', 'source': 'table', 'hand': 'right'}, 0)
        candidate = Registry(cfg.skills).resolve(call, {o.id: o for o in cfg.scene.objects})
        authority = ControlAuthority()
        lease = authority.acquire(call, candidate)
        running = asyncio.create_task(backend.run_policy(call, candidate, lease, authority))
        await entered.wait()
        authority.revoke()
        finish.set()
        self.assertEqual(await running, ExecutionStatus.CANCELLED)
        self.assertEqual([c.args[0] for c in backend.request.call_args_list], ['policy_begin'])


@unittest.skipIf(np is None, 'numpy optional simulation dependency is required')
class JointControlTests(unittest.TestCase):
    def test_actions_drive_joints_and_gripper_without_ik(self):
        # No IK model or motion-program methods exist in this fake world.
        qpos = np.r_[np.zeros(7), [.04, .04]]
        world = SimpleNamespace(config={'control_hz': 60}, target=qpos.copy(), stir_active=False,
            robot=SimpleNamespace(get_qpos=lambda: qpos.copy(), get_qlimits=lambda: np.tile([-2., 2.], (9, 1))))
        controller = JointPolicyControl(world)
        iterator = controller.run([[1.] * 8, [.02] * 7 + [0]], 20, .06, .01)
        next(iterator)
        np.testing.assert_allclose(world.target[:7], .06)
        np.testing.assert_allclose(world.target[7:], .03)
        self.assertEqual(1 + sum(1 for _ in iterator), 6)
        self.assertTrue(controller.applied[0]['arm_limited'])
        np.testing.assert_allclose(world.target[:7], .02)
        controller.stop()
        np.testing.assert_allclose(world.target[:7], qpos[:7])
        np.testing.assert_allclose(world.target[7:], .04)

    def test_invalid_chunk_never_changes_targets(self):
        world = SimpleNamespace(target=np.zeros(9))
        for bad in ([[0.] * 7], [[float('nan')] * 8], []):
            with self.assertRaises(ValueError):
                next(JointPolicyControl(world).run(bad, 20, .06, .01))
            np.testing.assert_array_equal(world.target, np.zeros(9))
