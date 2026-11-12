import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from primitive_vla.contracts import Domain, Record
from primitive_vla.interfaces import TaskService
from primitive_vla.memory import Memory
from primitive_vla.planning import Decision
from primitive_vla.planning.context import ContextBuilder
from primitive_vla.runtime.agent import Agent
from primitive_vla.runtime.session import Limits
from primitive_vla.skills import SkillStore
from primitive_vla.tools.contracts import ToolResult, Verification
from primitive_vla.tools.execution import ResourceManager
from primitive_vla.tools.registry import ToolRegistry
from tests.support import Provider, ScriptedPlanner, spec


def finish(message="done"):
    return Decision("finish", {"message": message, "outcome": "success"})


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.memory = Memory(self.directory.name)
        self.registry = ToolRegistry()
        self.skills = SkillStore()
        self.resources = ResourceManager()
        self.resources.define_robot("r", {"base", "arm"})
        self.agent = None

    async def asyncTearDown(self):
        if self.agent:
            for entry in self.registry._tools.values():
                entry.provider.confirms_stop = True
            await self.agent.close()
        else:
            self.memory.close()
        self.directory.cleanup()

    def make_agent(self, planner, limits=Limits()):
        self.agent = Agent(planner, self.registry, self.memory, self.skills, self.resources, limits)
        return self.agent.create_session("完成用户目标", "r", "robot")

    async def test_model_can_fix_interface_errors_from_feedback(self):
        provider = Provider()
        self.registry.register(spec(), provider)

        class FeedbackPlanner:
            async def decide(self, context):
                result = context.data["last_result"]
                if not result:
                    return Decision("call_tool", {"name": "state.inspect", "arguments": {}})
                if result.get("error"):
                    self.error = result["error"]
                    return Decision("call_tool", {"name": "state.inspect", "arguments": {"value": 1}})
                return finish()

        planner = FeedbackPlanner()
        session = self.make_agent(planner)
        await self.agent.run(session.id)
        self.assertEqual(session.status, "completed")
        self.assertEqual(provider.executions, 1)
        self.assertEqual(planner.error["kind"], "interface_error")
        self.assertEqual(session.tool_calls, 2)

    async def test_new_tool_registered_after_agent_creation_needs_no_loop_change(self):
        planner = ScriptedPlanner(Decision("discover_tools"), Decision("call_tool", {
            "name": "custom_capability", "arguments": {"value": 7}}), finish())
        session = self.make_agent(planner)
        provider = Provider()
        self.registry.register(spec("custom_capability"), provider)
        await self.agent.run(session.id)
        self.assertEqual(session.status, "completed")
        self.assertEqual(provider.executions, 1)
        self.assertEqual(planner.contexts[1].data["last_result"]["tools"][0]["name"], "custom_capability")

    async def test_skill_is_loaded_as_text_without_executable_steps(self):
        root = Path(self.directory.name) / "skills"
        root.mkdir()
        (root / "skills.json").write_text(json.dumps([
            {"id": "inspect", "version": "1.0", "description": "Observe if needed", "path": "inspect.md"}
        ]))
        content = "# Capability\nRead state when uncertain; choose an appropriate tool."
        (root / "inspect.md").write_text(content)
        self.skills.load(root)
        planner = ScriptedPlanner(Decision("read_skill", {"id": "inspect"}), finish())
        session = self.make_agent(planner)
        await self.agent.run(session.id)
        self.assertEqual(planner.contexts[1].data["last_result"]["content"], content)
        self.assertEqual(session.tool_calls, 0)

    async def test_verification_failure_can_trigger_new_observation(self):
        self.registry.register(spec("execution.act", Domain.EXECUTION), Provider())
        self.registry.register(spec("verification.check", Domain.VERIFICATION), Provider(ToolResult(
            {"value": 0}, verification=Verification("object", "failure", "object was not moved"))))
        observer = Provider()
        self.registry.register(spec(), observer)

        class EvidencePlanner:
            async def decide(self, context):
                last = context.data["last_result"]
                if not last:
                    return Decision("call_tool", {"name": "execution.act", "arguments": {"value": 1}})
                if last.get("name") == "execution.act":
                    return Decision("call_tool", {"name": "verification.check", "arguments": {"value": 1}})
                if last.get("result", {}).get("verification", {}).get("conclusion") == "failure":
                    return Decision("call_tool", {"name": "state.inspect", "arguments": {"value": 1}})
                return Decision("finish", {"message": "Need another approach", "outcome": "failed"})

        session = self.make_agent(EvidencePlanner())
        await self.agent.run(session.id)
        self.assertEqual(observer.executions, 1)
        self.assertEqual(session.status, "failed")

    async def test_wait_wakes_for_async_completion_and_does_not_poll_planner(self):
        provider = Provider(blocked=True)
        self.registry.register(spec("execution.act", Domain.EXECUTION, execution_mode="async",
                                    required_resources=("base",)), provider)
        waiting = asyncio.Event()

        class WaitingPlanner:
            def __init__(self):
                self.count = 0

            async def decide(self, context):
                self.count += 1
                if self.count == 1:
                    return Decision("call_tool", {"name": "execution.act", "arguments": {"value": 1}})
                if self.count == 2:
                    waiting.set()
                    return Decision("wait")
                return finish()

        planner = WaitingPlanner()
        session = self.make_agent(planner)
        task = asyncio.create_task(self.agent.run(session.id))
        await waiting.wait()
        await asyncio.sleep(0.02)
        self.assertEqual(planner.count, 2)
        provider.release.set()
        await asyncio.wait_for(task, 1)
        self.assertEqual(session.status, "completed")
        self.assertEqual(planner.count, 3)

    async def test_wait_does_not_lose_event_arriving_during_inference(self):
        entered, release = asyncio.Event(), asyncio.Event()

        class Planner:
            def __init__(self):
                self.count = 0

            async def decide(self, context):
                self.count += 1
                if self.count == 1:
                    entered.set()
                    await release.wait()
                    return Decision("wait")
                return finish()

        session = self.make_agent(Planner())
        task = asyncio.create_task(self.agent.run(session.id))
        await entered.wait()
        self.agent.events.wake(session.id)
        release.set()
        await asyncio.wait_for(task, 1)
        self.assertEqual(session.status, "completed")

    async def test_pause_interrupts_inference_and_resume_preserves_budget(self):
        entered = asyncio.Event()

        class SlowPlanner:
            async def decide(self, context):
                entered.set()
                await asyncio.Event().wait()

        session = self.make_agent(SlowPlanner())
        task = asyncio.create_task(self.agent.run(session.id))
        await entered.wait()
        self.agent.pause(session.id)
        await asyncio.wait_for(task, 1)
        self.assertEqual(session.status, "paused")
        self.agent.planner = ScriptedPlanner(finish())
        await self.agent.run(session.id)
        self.assertEqual(session.decisions, 2)
        self.assertEqual(session.status, "completed")

    async def test_pause_during_sync_tool_preserves_control_until_provider_ends(self):
        provider = Provider(blocked=True)
        self.registry.register(spec("execution.act", Domain.EXECUTION, required_resources=("base",)), provider)
        session = self.make_agent(ScriptedPlanner(Decision("call_tool", {
            "name": "execution.act", "arguments": {"value": 1}})))
        task = asyncio.create_task(self.agent.run(session.id))
        await provider.started.wait()
        self.agent.pause(session.id)
        await asyncio.wait_for(task, 1)
        self.assertEqual(session.status, "paused")
        self.assertIn("base", self.resources.occupied("r"))
        self.assertEqual(provider.cancellations, 0)
        call = self.agent.router.active(session.id)[0]
        provider.release.set()
        await asyncio.wait_for(call.done.wait(), 1)
        self.agent.planner = ScriptedPlanner(finish())
        await self.agent.run(session.id)
        self.assertEqual(session.status, "completed")

    async def test_session_deadline_interrupts_sync_tool_and_confirms_stop(self):
        provider = Provider(blocked=True)
        self.registry.register(spec("execution.act", Domain.EXECUTION, required_resources=("base",)), provider)
        session = self.make_agent(ScriptedPlanner(Decision("call_tool", {
            "name": "execution.act", "arguments": {"value": 1}})), Limits(max_active_seconds=0.03))
        await asyncio.wait_for(self.agent.run(session.id), 1)
        self.assertEqual(session.status, "budget_exhausted")
        self.assertEqual(provider.cancellations, 1)
        self.assertEqual(self.resources.occupied("r"), {})

    async def test_unconfirmed_stop_keeps_session_stopping_until_reconciled(self):
        provider = Provider(blocked=True, confirms_stop=False)
        self.registry.register(spec("execution.act", Domain.EXECUTION, required_resources=("base",)), provider)
        session = self.make_agent(ScriptedPlanner(Decision("call_tool", {
            "name": "execution.act", "arguments": {"value": 1}})), Limits(max_active_seconds=0.03))
        await asyncio.wait_for(self.agent.run(session.id), 1)
        self.assertEqual(session.status, "stopping")
        self.assertEqual(session.pending_status, "budget_exhausted")
        self.assertIn("base", self.resources.occupied("r"))
        provider.confirms_stop = True
        await self.agent.reconcile(session.id)
        self.assertEqual(session.status, "budget_exhausted")

    async def test_pause_during_pre_call_hook_does_not_leave_a_control_lease(self):
        entered = asyncio.Event()

        async def hook(stage, record):
            if stage == "before_call":
                entered.set()
                await asyncio.Event().wait()

        provider = Provider()
        self.registry.register(spec("execution.act", Domain.EXECUTION, required_resources=("base",)), provider)
        session = self.make_agent(ScriptedPlanner(Decision("call_tool", {
            "name": "execution.act", "arguments": {"value": 1}})))
        self.agent.hooks.add(Domain.EXECUTION, hook)
        task = asyncio.create_task(self.agent.run(session.id))
        await entered.wait()
        self.agent.pause(session.id)
        await asyncio.wait_for(task, 1)
        self.assertEqual(provider.executions, 0)
        self.assertEqual(self.resources.occupied("r"), {})

    async def test_user_interaction_carries_map_reference_and_selected_points(self):
        ref = self.memory.put_resource(b"map preview", "image/png")
        planner = ScriptedPlanner(Decision("ask_user", {"question": "选择 A、B 点", "resource_ids": [ref.id]}), finish())
        session = self.make_agent(planner)
        await self.agent.run(session.id)
        self.assertEqual(session.status, "waiting_user")
        self.assertEqual(session.last_result["resources"][0]["id"], ref.id)
        self.agent.respond(session.id, "选好了", {"A": [1, 2], "B": [3, 4]})
        await self.agent.run(session.id)
        self.assertEqual(planner.contexts[1].data["last_result"]["payload"]["A"], [1, 2])
        self.assertEqual(session.tool_calls, 0)

    async def test_reasoning_and_retry_budgets_bound_bad_decisions(self):
        planner = ScriptedPlanner(*(Decision("not_an_action") for _ in range(10)))
        session = self.make_agent(planner, Limits(max_consecutive_errors=2))
        await self.agent.run(session.id)
        self.assertEqual(session.status, "budget_exhausted")
        self.assertEqual(session.decisions, 2)

    async def test_tool_budget_counts_rejected_calls(self):
        planner = ScriptedPlanner(*(Decision("call_tool", {"name": "missing", "arguments": {}}) for _ in range(3)))
        session = self.make_agent(planner, Limits(max_tool_calls=1))
        await self.agent.run(session.id)
        self.assertEqual(session.status, "budget_exhausted")
        self.assertEqual(session.tool_calls, 1)

    async def test_cannot_finish_while_execution_is_active(self):
        provider = Provider(blocked=True)
        self.registry.register(spec("execution.act", Domain.EXECUTION, execution_mode="async"), provider)
        planner = ScriptedPlanner(Decision("call_tool", {"name": "execution.act", "arguments": {"value": 1}}),
                                  finish(), Decision("ask_user", {"question": "Wait or cancel?"}))
        session = self.make_agent(planner)
        await self.agent.run(session.id)
        self.assertEqual(session.status, "waiting_user")
        self.assertEqual(planner.contexts[2].data["last_result"]["kind"], "resource_conflict")

    async def test_cancel_waiting_session_stops_control_before_terminal_state(self):
        provider = Provider(blocked=True)
        self.registry.register(spec("execution.act", Domain.EXECUTION, execution_mode="async",
                                    required_resources=("base",)), provider)
        wait_entered = asyncio.Event()

        class Planner:
            async def decide(self, context):
                if not context.data["last_result"]:
                    return Decision("call_tool", {"name": "execution.act", "arguments": {"value": 1}})
                wait_entered.set()
                return Decision("wait")

        session = self.make_agent(Planner())
        task = asyncio.create_task(self.agent.run(session.id))
        await wait_entered.wait()
        await self.agent.cancel(session.id)
        await asyncio.wait_for(task, 1)
        self.assertEqual(session.status, "cancelled")
        self.assertEqual(self.resources.occupied("r"), {})

    async def test_context_is_bounded_and_keeps_resource_references(self):
        session = self.make_agent(ScriptedPlanner(finish()))
        ref = self.memory.put_resource(b"image", "image/png")
        for number in range(40):
            self.memory.write(Record(session.id, session.task_id, "stream", Domain.STATE,
                                     "observation", "observed", {"large": "x" * 30000, "number": number},
                                     resource_refs=(ref,)))
        context = ContextBuilder(self.memory, self.registry, self.skills, 12000).build(session, [])
        size = len(json.dumps(context.data, ensure_ascii=False)) + len(json.dumps(context.action_schema, ensure_ascii=False))
        self.assertLessEqual(size, 12000)
        self.assertLess(len(context.data["records"]), 40)
        self.assertEqual(context.data["records"][0]["resource_refs"][0]["id"], ref.id)

    async def test_planning_cannot_rewrite_tool_evidence(self):
        record = self.memory.write(Record("s", "t", "verify", Domain.VERIFICATION,
                                          "verification", "completed", {"conclusion": "failure"}))
        planner = ScriptedPlanner(Decision("memory_update", {"id": record.id,
                                  "changes": {"payload": {"conclusion": "success"}}}), finish())
        session = self.make_agent(planner)
        await self.agent.run(session.id)
        self.assertEqual(self.memory.get(record.id).payload, {"conclusion": "failure"})
        self.assertEqual(planner.contexts[1].data["last_result"]["kind"], "permission_denied")

    async def test_planner_can_forget_short_memory_without_context_echoes(self):
        marker = "discard-this-working-note"

        class Planner:
            step = 0

            async def decide(inner, context):
                inner.step += 1
                if inner.step == 1:
                    return Decision("memory_write", {"title": "Temporary", "content": marker})
                if inner.step == 2:
                    inner.note_id = context.data["last_result"]["id"]
                    return Decision("memory_delete", {"id": inner.note_id})
                if inner.step == 3:
                    self.assertNotIn(marker, json.dumps(context.data))
                    return Decision("memory_search", {"query": marker})
                self.assertEqual(context.data["last_result"]["records"], [])
                return finish()

        planner = Planner()
        session = self.make_agent(planner)
        await self.agent.run(session.id)
        self.assertEqual(session.status, "completed")
        self.assertEqual(self.memory.evidence(planner.note_id).payload["content"], marker)

    async def test_persisted_session_resumes_without_resetting_counts(self):
        session = self.make_agent(ScriptedPlanner(Decision("ask_user", {"question": "Where?"})))
        await self.agent.run(session.id)
        self.agent.sessions.clear()
        loaded = self.agent.load_session(session.id)
        self.assertEqual(loaded.status, "waiting_user")
        self.assertEqual(loaded.decisions, 1)
        self.agent.respond(session.id, "B point")
        self.agent.planner = ScriptedPlanner(finish())
        await self.agent.run(session.id)
        self.assertEqual(loaded.decisions, 2)

    async def test_service_can_start_and_await_result(self):
        self.make_agent(ScriptedPlanner(finish()))
        service = TaskService(self.agent)
        session = service.start("report", "r", "robot")
        await service.tasks[session.id]
        self.assertEqual(session.status, "completed")

    async def test_service_immediate_pause_does_not_start_a_decision(self):
        planner = ScriptedPlanner(finish())
        self.make_agent(planner)
        service = TaskService(self.agent)
        session = service.start("report", "r", "robot")
        await service.pause(session.id)
        self.assertEqual(session.status, "paused")
        self.assertEqual(session.decisions, 0)
        await service.resume(session.id)
        self.assertEqual(session.status, "completed")

    async def test_service_immediate_cancel_prevents_start(self):
        self.make_agent(ScriptedPlanner(finish()))
        service = TaskService(self.agent)
        session = service.start("report", "r", "robot")
        await service.cancel(session.id)
        self.assertEqual(session.status, "cancelled")
        self.assertEqual(session.decisions, 0)
