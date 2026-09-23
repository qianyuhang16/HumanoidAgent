"""Execution safety and independent outcome verification through public seams."""

import asyncio
from dataclasses import replace
from time import time
import unittest

from primitive_vla.agent.contracts import AgentContext, VerificationRequest
from primitive_vla.agent.core import Agent
from primitive_vla.agent.decision import RuleDecisionModel
from primitive_vla.agent.memory import InMemory
from primitive_vla.backends.mock import MockBackend
from primitive_vla.domain.models import EffectStatus, ExecutionStatus, Fact, Goal, Observation, SkillCall, Task, Truth
from primitive_vla.domain.skills import Candidate, Registry, SkillSpec
from primitive_vla.domain.state import State
from primitive_vla.execution.adapter import ExecutiveExecutor
from primitive_vla.execution.executive import Executive
from primitive_vla.storage.events import EventLog
from primitive_vla.verification.rules import RuleVerifier


class ActionDriver:
    """Deliberately has no sensing, warmup, or persistence interface."""

    model_version = "test-driver"

    def __init__(self, backend, *, stalled=False, acknowledge=True):
        self.backend = backend
        self.stalled = stalled
        self.acknowledge = acknowledge
        self.entered = asyncio.Event()
        self.lease = None

    async def run(self, call, candidate, lease, authority, feedback):
        self.lease = lease
        self.entered.set()
        if self.stalled:
            await asyncio.Event().wait()
        return await self.backend.run(call, candidate, lease, authority, feedback)

    async def stop(self, call_id, reason):
        await self.backend.stop(call_id, reason)
        return self.acknowledge


class ExecutiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.spec = SkillSpec("DONE", "1", {}, {"healthy": "true"}, {"healthy": "true"},
                              {"done": "true"}, [], "Finish", timeout_s=1)
        self.registry = Registry([self.spec])
        self.backend = MockBackend({"healthy": Truth.TRUE, "done": Truth.FALSE}, delay_s=0)
        self.state = State({})
        self.state.update(await self.backend.observe())
        self.log = EventLog()
        self.driver = ActionDriver(self.backend)
        self.executive = Executive(self.registry, self.state, self.driver, self.log,
                                   poll_s=.001, stop_timeout_s=.05, observer=self.backend)
        self.task = Task("Finish", [Goal("done", {"done": Truth.TRUE})], max_observations=4)

    def call(self):
        return SkillCall("DONE", {}, self.state.version)

    async def verify(self, result):
        return await RuleVerifier().verify(VerificationRequest(
            self.task, self.state, result, Candidate(self.spec, {})))

    async def test_action_only_driver_and_separate_verifier(self):
        result = await self.executive.execute(self.call())
        self.assertEqual(result.execution, ExecutionStatus.COMPLETED)
        self.assertEqual(result.effect, EffectStatus.UNKNOWN)
        self.assertEqual(result.evidence, {})
        self.assertTrue(result.needs_observation)
        self.assertIsNotNone(result.started_at)
        # Executive must not consume the post-action observation.
        self.assertEqual(self.state.values()["done"], Truth.FALSE)
        self.state.update(await self.backend.observe())
        self.assertEqual((await self.verify(result)).status, "success")
        self.assertEqual(result.effect, EffectStatus.UNKNOWN)
        self.assertIsNone(self.executive.control.lease)
        self.assertNotIn("verifying", [e["event"] for e in self.log.events])

    async def test_motion_completion_does_not_establish_effect_success(self):
        self.spec.mock_faults.append("missed_grasp")
        self.backend.fault = "missed_grasp"
        result = await self.executive.execute(self.call())
        self.state.update(await self.backend.observe())
        self.assertEqual(result.execution, ExecutionStatus.COMPLETED)
        self.assertEqual((await self.verify(result)).status, "failure")

    async def test_verifier_rejects_retained_pre_action_facts(self):
        self.state.update(Observation({"done": Fact(Truth.TRUE, "old", ttl_s=None)}))
        del self.backend.world["done"]
        # Keep the driver from changing this fact during the action.
        self.spec.mock_faults.append("missed_grasp")
        self.backend.fault = "missed_grasp"
        result = await self.executive.execute(self.call())
        # State can retain facts omitted by the next observation.
        self.state.update(await self.backend.observe())
        self.assertEqual(self.state.values()["done"], Truth.TRUE)
        self.assertEqual((await self.verify(result)).status, "unknown")

    async def test_verifier_preserves_confidence_and_expiry_checks(self):
        result = await self.executive.execute(self.call())
        for fact in (Fact(Truth.TRUE, "camera", confidence=.1),
                     Fact(Truth.TRUE, "camera", ttl_s=0)):
            with self.subTest(fact=fact):
                self.state.update(Observation({"done": fact}))
                self.assertEqual((await self.verify(result)).status, "unknown")

    async def test_duplicate_call_is_not_dispatched_twice(self):
        call = self.call()
        first, second = await asyncio.gather(self.executive.execute(call), self.executive.execute(call))
        self.assertIs(first, second)
        self.assertEqual(self.backend.executions, ["DONE"])
        conflicting = await self.executive.execute(replace(call, deadline=call.deadline + 1))
        self.assertEqual(conflicting.execution, ExecutionStatus.REJECTED)

    async def test_stale_call_and_failed_preconditions_do_not_dispatch(self):
        stale = replace(self.call(), state_version=-1)
        self.assertEqual((await self.executive.execute(stale)).execution, ExecutionStatus.REJECTED)
        self.backend.world["healthy"] = Truth.FALSE
        self.assertEqual((await self.executive.execute(self.call())).execution, ExecutionStatus.REJECTED)
        self.assertEqual(self.backend.executions, [])
        self.assertIsNone(self.executive.control.lease)

    async def test_lost_invariant_stops_and_fences_driver(self):
        self.driver.stalled = True
        pending = asyncio.create_task(self.executive.execute(self.call()))
        await asyncio.wait_for(self.driver.entered.wait(), 1)
        self.backend.world["healthy"] = Truth.FALSE
        result = await asyncio.wait_for(pending, 1)
        self.assertEqual(result.execution, ExecutionStatus.FAILED)
        self.assertEqual(self.backend.stop_calls, [result.call_id])
        self.assertFalse(self.executive.control.accepts(result.call_id, self.driver.lease.generation))

    async def test_timeout_without_stop_ack_keeps_control_locked(self):
        self.driver.stalled = True
        self.driver.acknowledge = False
        result = await self.executive.execute(replace(self.call(), deadline=time() + .05))
        self.assertEqual(result.execution, ExecutionStatus.TIMED_OUT)
        self.assertIsNotNone(self.executive.control.lease)
        self.assertTrue(self.executive.control.revoked)
        self.assertEqual((await self.executive.execute(self.call())).execution, ExecutionStatus.REJECTED)

    async def test_cancelling_caller_stops_before_propagating(self):
        self.driver.stalled = True
        pending = asyncio.create_task(self.executive.execute(self.call()))
        await asyncio.wait_for(self.driver.entered.wait(), 1)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 1)
        self.assertEqual(len(self.backend.stop_calls), 1)
        self.assertIsNone(self.executive.control.lease)

    async def test_combined_backend_remains_supported(self):
        executive = Executive(self.registry, self.state, self.backend)
        self.assertEqual((await executive.execute(self.call())).execution, ExecutionStatus.COMPLETED)

    async def test_agent_records_independent_verdict(self):
        context = AgentContext(self.state, self.backend, self.registry, RuleDecisionModel(),
                               ExecutiveExecutor(self.executive), RuleVerifier(), InMemory(), self.log)
        report = await Agent(max_observations=4).run(self.task, context)
        self.assertEqual(report.status, "completed")
        self.assertEqual(report.results[0]["effect"], EffectStatus.UNKNOWN)
        self.assertEqual(report.results[0]["verification"]["status"], "success")
        self.assertEqual(len([e for e in self.log.events if e["event"] == "skill_verified"]), 1)
