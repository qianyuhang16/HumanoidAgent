"""Control observations must remain independent of the native task answer."""
import io
import time
import unittest
from types import SimpleNamespace

from primitive_vla.plugins.grounded_sam.backend import bind_targets
from primitive_vla.simulation.rmbench_sensors import SensorWorld
from primitive_vla.skills.loader import load_skills
from primitive_vla.providers.codex_schema import output_schema

try:
    import numpy as np
except ImportError:
    np = None


class Poison:
    def __getattr__(self, key):
        raise AssertionError(f'Privileged task access: {key}')


class SensorIsolationTests(unittest.TestCase):
    def test_observation_never_reads_task_adapter_or_evaluator(self):
        world = SensorWorld.__new__(SensorWorld)
        world.task = Poison()
        world.env = SimpleNamespace(take_action_cnt=5, step_lim=100)
        world.current = {'joint_action': {'vector': SimpleNamespace(tolist=lambda: [0]*14)}}
        world.chunks, world.ik_moves, world.frame = 1, 1, 'frame.png'
        result = world.observe()
        self.assertEqual(result['facts'], {})
        self.assertEqual(set(result['proprioception']), {'qpos', 'control_steps', 'remaining_action_steps', 'chunks', 'ik_moves'})
        self.assertFalse(world.task_terminated())
        self.assertEqual(world.task_feedback(), {})

    @unittest.skipIf(np is None, 'NumPy is an optional model/simulation dependency')
    def test_chunks_ignore_native_success_and_return_no_task_verdict(self):
        world = SensorWorld.__new__(SensorWorld)
        class Env:
            take_action_cnt = 0
            step_lim = 100
            @property
            def eval_success(self):
                raise AssertionError('Native verdict must not gate chunks')
            def take_action(self, action, action_type):
                self.take_action_cnt += 1
            def get_obs(self):
                return {}
        world.np, world.env, world.task = np, Env(), Poison()
        world.active, world.generation, world.deadline = 'call', 1, time.time()+10
        world.skill_start, world.chunks, world.ik_moves = 0, 0, 0
        world.config = {'end_threshold': 2, 'max_skill_steps': 50}
        world.policy = SimpleNamespace(end_count=0, update=lambda *a: None)
        world.capture, world.prompt, world.frame, world.trace = lambda: None, 'test', 'frame.png', io.StringIO()
        result = world.chunk('call', 1, [[0]*14]*3)
        self.assertEqual(result['executed'], 3)
        self.assertFalse({'success', 'fail_flag', 'stage', 'reward'} & result.keys())

    def test_evaluation_is_terminal_and_requires_stopped_control(self):
        world = SensorWorld.__new__(SensorWorld)
        world.env = object()
        world.native_evaluator = lambda env: True
        world.active = 'call'
        with self.assertRaises(RuntimeError):
            world.evaluate()
        world.active = None
        self.assertTrue(world.evaluate()['success'])
        self.assertTrue(world.finalized)

    def test_ambiguous_or_missing_detections_do_not_produce_a_target(self):
        config = {'target_label': 'blue battery'}
        def detection(x):
            return dict(label='blue battery', surface_world=[x, 0, .8], mask_score=.95)
        self.assertEqual(bind_targets([detection(-.2)], config), {})
        self.assertEqual(bind_targets([detection(-.2), detection(0), detection(.2)], config), {})
        self.assertEqual(bind_targets([detection(0), detection(.01)], config), {})
        self.assertEqual(bind_targets([detection(.2), detection(-.2)], config)['left'], [-.2, 0, .8])
        proposals = [dict(detection(x), id=i) for i, x in enumerate([-.2, 0, .2])]
        self.assertEqual(bind_targets(proposals, config, [0, 2])['right'], [.2, 0, .8])
        self.assertEqual(bind_targets(proposals, config, [99]), {})

    @unittest.skipIf(np is None, 'NumPy is an optional model/simulation dependency')
    def test_rgbd_projection_uses_calibration_and_rejects_invalid_depth(self):
        from primitive_vla.plugins.grounded_sam.model import surface_point
        depth = np.ones((3, 3)) * 2
        mask = np.ones_like(depth, dtype=bool)
        intrinsic = np.array([[2, 0, 1], [0, 2, 1], [0, 0, 1]])
        extrinsic = np.eye(4)
        extrinsic[0, 3] = -3
        np.testing.assert_allclose(surface_point(mask, depth, intrinsic, extrinsic), [3, 0, 2])
        self.assertIsNone(surface_point(mask, depth*0, intrinsic, extrinsic))

    def test_skill_contracts_do_not_claim_manipulation_as_observation(self):
        skills = load_skills(['builtin:rmbench/battery_visual', 'src/primitive_vla/plugins/grounded_sam/SKILL.md'])
        for skill in skills:
            if skill.backend == 'rmbench':
                self.assertEqual(skill.effects, {'inspection:needed': 'true'})
                self.assertNotIn('benchmark:success', skill.preconditions)
        self.assertTrue(any(s.name == 'INSPECT_SCENE' and s.program == [{'op': 'inspect'}] for s in skills))
        schema = output_schema('visual_inspection', {'criteria': {'visual:complete': 'visible'}})
        self.assertEqual(schema['properties']['predicates']['items']['properties']['predicate']['enum'], ['visual:complete'])


class AdaptiveInterfaceTests(unittest.IsolatedAsyncioTestCase):
    def test_one_visible_object_can_be_bound_without_guessing_the_hidden_one(self):
        from primitive_vla.plugins.grounded_sam.backend import bind_partial_targets
        detections = [dict(id=7, label='blue battery', surface_world=[.03, -.1, .78], mask_score=.9)]
        assignment = dict(side='right', detection_id=7, confidence=.9, evidence='visible')
        self.assertEqual(bind_partial_targets(detections, [assignment], {}), {'right': [.03, -.1, .78]})
        self.assertEqual(bind_partial_targets(detections, [dict(assignment, confidence=.2)], {}), {})
        self.assertEqual(bind_partial_targets(detections, [assignment, dict(assignment, side='left')], {}), {})

    async def test_model_memory_is_carried_but_never_promoted_to_measured_facts(self):
        from primitive_vla.agent.planner import StructuredPlanner
        from primitive_vla.domain.models import Goal, SceneObject, Task, Truth
        from primitive_vla.domain.skills import Registry, SkillSpec
        from primitive_vla.domain.state import State
        state = State({'object': SceneObject('object', 'item', 'visible object')})
        registry = Registry([SkillSpec('TURN', '1', {'position': ['item']}, {}, {},
            {'inspection': 'true'}, ['arm'], 'turn', policy_prompt='Turn {position}')])
        memory = {'hypothesis': 'Unconfirmed joint state', 'trials': [], 'uncertainties': ['identity']}
        contexts = []
        async def choose(context):
            contexts.append(context)
            return {'action': 'observe', 'reason': 'need evidence', 'working_memory': memory}
        planner = StructuredPlanner(choose, adaptive_memory=True)
        task = Task('test', [Goal('goal', {'finished': Truth.TRUE})])
        for _ in range(2):
            await planner.decide(task, state, registry.candidates(state.objects), [])
        self.assertEqual(contexts[1]['working_memory'], memory)
        self.assertEqual(contexts[1]['candidates'][0]['policy_prompt'], 'Turn object')
        self.assertEqual(state.facts, {})

    async def test_visual_reviewer_can_yield_before_the_vla_budget(self):
        from unittest.mock import AsyncMock
        from primitive_vla.backends.rmbench_visual import VisualRMBenchBackend
        from primitive_vla.domain.models import ExecutionStatus, Observation
        from primitive_vla.storage.events import EventLog
        backend = VisualRMBenchBackend.__new__(VisualRMBenchBackend)
        backend.config = {'mode': 'ik_approach', 'review_interval_steps': 160}
        backend.log, backend.targets, backend.estimates, backend.protocol = EventLog(), {}, {}, {}
        backend.perception_evidence = None
        backend.warmup = AsyncMock()
        backend.observe = AsyncMock(return_value=Observation({}, images={'simulation': 'current.png'}))
        backend.supervisor = AsyncMock(return_value={'action': 'yield', 'reason': 'No visible progress'})
        backend.stop = AsyncMock(return_value=True)
        backend.worker = SimpleNamespace(request=AsyncMock(side_effect=[{}, {'actions': [[0]*14]},
            {'skill_steps': 165, 'end_signals': 0, 'subtask_end': False, 'exhausted': False}]))
        call = SimpleNamespace(call_id='call', skill='TURN', bindings={'position': 'left'}, deadline=time.time()+30)
        candidate = SimpleNamespace(spec=SimpleNamespace(policy_prompt='Turn {position}'))
        result = await backend.run(call, candidate, SimpleNamespace(generation=1),
                                   SimpleNamespace(accepts=lambda *a: True), lambda message: None)
        self.assertEqual(result, ExecutionStatus.COMPLETED)
        self.assertEqual(backend.worker.request.await_count, 3)
        backend.stop.assert_awaited_once_with('call', 'Agent requested visual reassessment')
        self.assertEqual(backend.supervisor.await_args.args[0]['actions'], ['continue', 'yield'])
