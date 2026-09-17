"""Measured battery feedback must not disclose the hidden solution."""

import unittest
from types import SimpleNamespace

from primitive_vla.simulation.rmbench_tasks import BatteryTry
from primitive_vla.skills.loader import load_skills
from primitive_vla.domain.skills import Registry
from primitive_vla.domain.models import SceneObject
from primitive_vla.simulation.approach import select_approach


class BatteryFactsTests(unittest.TestCase):
    def test_entry_fallback_plans_from_measured_position_without_manipulation(self):
        planned = []
        def plan(pose):
            planned.append(pose)
            return {'status': 'Success'}
        env = SimpleNamespace(choose_grasp_pose=lambda *a, **kw: (None, None),
                              robot=SimpleNamespace(left_plan_path=plan))
        actor = SimpleNamespace(get_pose=lambda: SimpleNamespace(p=[.2, -.1, .77]))
        pose, attempts = select_approach(env, actor, 'left', .1, .03,
            {'distances': [.08], 'tool_offset': .12, 'orientations': {'left': [[1, 0, 0, 0]]}})
        self.assertEqual(len(planned), 1)
        self.assertAlmostEqual(pose[2], .97)
        self.assertEqual(attempts[-1]['source'], 'world_vertical')
        self.assertTrue(attempts[-1]['reachable'])

    def test_measured_feedback_and_initialization_without_hidden_answer(self):
        class Environment:
            battery1 = SimpleNamespace(state=0, seated=False)
            battery2 = SimpleNamespace(state=1, seated=False)
            target_pose1_p, target_pose2_p = 'left_slot', 'right_slot'
            eval_success = False

            @property
            def correct_combination(self):
                raise AssertionError('Policy observation must not read the evaluator answer')

            def check_battery_in_slot(self, actor, target):
                return actor.seated

            def get_battery_state(self, actor):
                return actor.state

            def check_dashboard_on(self):
                return False

        env, task = Environment(), BatteryTry()
        self.assertFalse(task.facts(env)['initialized'])
        env.battery1.seated = env.battery2.seated = True
        facts = task.facts(env)
        self.assertTrue(facts['initialized'])
        self.assertTrue(facts['polarity:right:negative'])
        self.assertNotIn('correct_combination', facts)
        env.battery1.seated = False
        self.assertTrue(task.facts(env)['initialized'])
        self.assertEqual(task.stage(env), 1)

    def test_battery_skill_bindings_are_external_contracts(self):
        registry = Registry(load_skills(['builtin:rmbench/battery']))
        objects = {name: SceneObject(name, kind, name) for kind, names in {
            'battery_pair':['pair'], 'battery_side':['left','right'],
            'battery_direction':['positive','negative']}.items() for name in names}
        candidates = registry.candidates(objects)
        self.assertEqual(len(candidates), 5)
        self.assertEqual(sum(c.spec.name == 'INSTALL_PAIR' for c in candidates), 1)
