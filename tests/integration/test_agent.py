"""Behavioral checks for the integration boundaries, using only unittest."""

import asyncio
import unittest
from dataclasses import replace
from time import time

from primitive_vla.agent.task_parser import DEFAULT_INSTRUCTION, parse_instruction
from primitive_vla.config.loader import default_config
from primitive_vla.domain.state import State
from primitive_vla.agent.loop import Agent
from primitive_vla.backends.mock import MockBackend
from primitive_vla.execution.executive import Executive
from primitive_vla.domain.models import EffectStatus, ExecutionStatus, Fact, Observation, SkillCall, Truth
from primitive_vla.agent.planner import GoalPlanner, StructuredPlanner
from primitive_vla.domain.skills import Registry


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def make_system(self, fault="none", backend_type=MockBackend, **options):
        config = default_config()
        state = State({o.id: o for o in config.scene.objects})
        initial = config.scene.facts
        backend = backend_type(initial, fault=fault, **options)
        state.update(await backend.observe())
        registry = Registry(config.skills)
        executive = Executive(registry, state, {"mock": backend}, poll_s=0.005, stop_timeout_s=0.05)
        return state, backend, executive

    def place_call(self, state, **options):
        return SkillCall("PLACE", {"object": "spoon", "target": "spoon_home", "hand": "right"}, state.version, **options)

    async def test_kitchen_releases_hand_then_pours_stirs_and_restores(self):
        state, backend, executive = await self.make_system()
        report = await Agent(GoalPlanner(), executive, backend).run(parse_instruction(DEFAULT_INSTRUCTION, state))
        self.assertEqual(report["status"], "completed")
        self.assertEqual(backend.executions, ["PLACE", "GRASP", "POUR", "PLACE", "GRASP", "STIR", "PLACE"])
        self.assertEqual(state.values()["free:right"], Truth.TRUE)
        self.assertEqual(state.values()["free:left"], Truth.TRUE)
        self.assertIsNone(executive.control.lease)

    async def test_open_box_is_skipped_but_final_close_is_required(self):
        state, backend, executive = await self.make_system()
        backend.world.update({"open:box": Truth.TRUE, "accepts:box": Truth.TRUE})
        report = await Agent(GoalPlanner(), executive, backend).run(parse_instruction("水果装盒", state))
        self.assertEqual(report["status"], "completed")
        self.assertNotIn("OPEN", backend.executions)
        self.assertEqual(backend.executions[-1], "CLOSE")
        self.assertEqual(state.values()["at:red_fruit:box"], Truth.TRUE)

    async def test_uncertain_pour_is_observed_without_repeating(self):
        state, backend, executive = await self.make_system("uncertain_pour")
        report = await Agent(GoalPlanner(), executive, backend).run(parse_instruction(DEFAULT_INSTRUCTION, state))
        self.assertEqual(report["status"], "completed")
        pour = next(r for r in report["results"] if r["skill"] == "POUR")
        self.assertEqual((pour["execution"], pour["effect"]), (ExecutionStatus.COMPLETED, EffectStatus.UNKNOWN))
        self.assertEqual(backend.executions.count("POUR"), 1)
        self.assertEqual(report["reobservations"], 1)

    async def test_motion_completion_is_not_grasp_success_and_retry_is_bounded(self):
        state, backend, executive = await self.make_system("missed_grasp")
        report = await Agent(GoalPlanner(), executive, backend).run(parse_instruction(DEFAULT_INSTRUCTION, state))
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["results"][1]["effect"], EffectStatus.FAILURE)
        self.assertEqual(backend.executions.count("GRASP"), 3)

    async def test_concurrent_duplicate_runs_once_and_payload_collision_rejects(self):
        state, backend, executive = await self.make_system()
        call = self.place_call(state)
        first, second = await asyncio.gather(executive.execute(call), executive.execute(call))
        self.assertEqual(first, second)
        self.assertEqual(backend.executions, ["PLACE"])
        collision = await executive.execute(replace(call, bindings=call.bindings | {"target": "table"}))
        self.assertEqual(collision.execution, ExecutionStatus.REJECTED)

    async def test_stale_state_and_unsupported_hand_never_dispatch(self):
        state, backend, executive = await self.make_system()
        call = self.place_call(state)
        state.update(await backend.observe())
        self.assertEqual((await executive.execute(call)).execution, ExecutionStatus.REJECTED)
        unsupported = replace(self.place_call(state), bindings=call.bindings | {"hand": "left"})
        self.assertEqual((await executive.execute(unsupported)).execution, ExecutionStatus.REJECTED)
        self.assertEqual(backend.executions, [])

    async def test_unknown_entry_condition_requests_observation_never_assumes_true(self):
        state, backend, executive = await self.make_system()
        backend.world["free:right"] = Truth.UNKNOWN
        state.update(await backend.observe())
        call = SkillCall("GRASP", {"object": "blue_cup", "source": "cup_home", "hand": "right"}, state.version)
        result = await executive.execute(call)
        self.assertEqual(result.execution, ExecutionStatus.REJECTED)
        self.assertTrue(result.needs_observation)
        self.assertEqual(backend.executions, [])

    async def test_cancellation_preserves_held_object_and_drops_late_actions(self):
        state, backend, executive = await self.make_system(delay_s=0.2)
        call = self.place_call(state)
        pending = asyncio.create_task(executive.execute(call))
        for _ in range(100):
            if backend.executions:
                break
            await asyncio.sleep(0.001)
        old = executive.control.lease
        self.assertIsNotNone(old)
        self.assertTrue(executive.cancel(call.call_id))
        result = await pending
        self.assertEqual(result.execution, ExecutionStatus.CANCELLED)
        self.assertEqual(state.values()["holding:right:spoon"], Truth.TRUE)
        self.assertEqual(backend.stop_calls, [call.call_id])
        mutated = []
        candidate = executive.registry.resolve(self.place_call(state), state.objects)
        executive.control.acquire(self.place_call(state), candidate)
        self.assertFalse(executive.control.apply(old.call_id, old.generation, lambda: mutated.append(True)))
        self.assertEqual(mutated, [])
        executive.control.release()

    async def test_client_task_cancellation_stops_backend(self):
        state, backend, executive = await self.make_system(delay_s=0.2)
        pending = asyncio.create_task(executive.execute(self.place_call(state)))
        while not backend.executions:
            await asyncio.sleep(0.001)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(len(backend.stop_calls), 1)
        self.assertIsNone(executive.control.lease)
        self.assertEqual(state.values()["holding:right:spoon"], Truth.TRUE)

    async def test_timeout_without_stop_ack_keeps_control_locked(self):
        class UnacknowledgedBackend(MockBackend):
            async def stop(self, call_id, reason):
                self.stop_calls.append(call_id)
                return False

        state, backend, executive = await self.make_system(backend_type=UnacknowledgedBackend, delay_s=0.2)
        result = await executive.execute(self.place_call(state, deadline=time() + 0.02))
        self.assertEqual(result.execution, ExecutionStatus.TIMED_OUT)
        self.assertIsNotNone(executive.control.lease)
        self.assertTrue(executive.control.revoked)
        retry = await executive.execute(self.place_call(state))
        self.assertEqual(retry.execution, ExecutionStatus.REJECTED)
        self.assertEqual(backend.executions, ["PLACE"])

    async def test_monitor_stops_on_health_loss_without_planner(self):
        state, backend, executive = await self.make_system(delay_s=0.2)
        pending = asyncio.create_task(executive.execute(self.place_call(state)))
        while not backend.executions:
            await asyncio.sleep(0.001)
        backend.world["healthy"] = Truth.FALSE
        result = await pending
        self.assertEqual(result.execution, ExecutionStatus.FAILED)
        self.assertEqual(state.values()["holding:right:spoon"], Truth.TRUE)
        self.assertEqual(len(backend.stop_calls), 1)

    async def test_expired_and_out_of_order_evidence(self):
        state, _, _ = await self.make_system()
        now = time()
        state.update(Observation({"free:right": Fact(Truth.TRUE, "sensor", now, ttl_s=0.1)}))
        state.update(Observation({"free:right": Fact(Truth.FALSE, "old_sensor", now - 1)}))
        self.assertEqual(state.values()["free:right"], Truth.TRUE)
        self.assertEqual(state.facts["free:right"].current(now + 1), Truth.UNKNOWN)
        state.update(Observation({}))
        self.assertIn("holding:right:spoon", state.facts)

    async def test_structured_planner_rejects_unregistered_model_output(self):
        state, _, executive = await self.make_system()

        async def model(context):
            self.assertTrue(context["candidates"])
            return {"action": "skill", "index": 9999}

        with self.assertRaises(ValueError):
            await StructuredPlanner(model).decide(parse_instruction(DEFAULT_INSTRUCTION, state), state,
                                                   executive.registry.candidates(state.objects), [])


if __name__ == "__main__":
    unittest.main()
