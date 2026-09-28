"""Judgments share the Agent turn; optional reviewers never control dispatch."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx

from primitive_vla.agent.memory import Session
from primitive_vla.config.schema import AdapterSpec, AppConfig, AgentVerificationConfig
from primitive_vla.runtime.session import Runtime
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import FunctionTool, ToolDefinition, ToolResult


class ReviewableTools:
    def __init__(self, options, context):
        self.sent = []
        for name in ("before.jpg", "after.jpg", "left.jpg", "right.jpg", "fresh.jpg"):
            (context.workspace / "media" / name).write_bytes(name.encode())
        self.tools = [FunctionTool(ToolDefinition(name="effect.run", description="Place the selected object.",
            parameters={"type": "object", "properties": {"object": {"type": "string"}},
                        "required": ["object"], "additionalProperties": False},
            read_only=False, resources=["fixture"], review_outcome=True), self.run, self.stop),
            FunctionTool(ToolDefinition(name="camera.observe", description="Observe the current scene."), self.observe)]

    async def run(self, args, context):
        self.sent.append(args["object"])
        return ToolResult(data={"executed": True, "task_success": None}, before_images={"front": "before.jpg"},
                          images={"front": "after.jpg", "left": "left.jpg", "right": "right.jpg"})

    async def observe(self, args, context):
        return ToolResult(images={"front": "fresh.jpg"})

    async def stop(self, *_):
        return True


class WorldModelFixture:
    def __init__(self, options, context):
        self.contexts = []
        self.closed = False

    async def assess(self, context):
        self.contexts.append(context)
        return {"verdict": "satisfied", "reason": "Measured final placement", "evidence": [context.call["id"]]}

    async def close(self):
        self.closed = True


class BrokenReviewer:
    def __init__(self, options, context):
        raise AssertionError("Main mode must not construct the configured reviewer")


class SlowReviewer(WorldModelFixture):
    def __init__(self, options, context):
        super().__init__(options, context)
        self.started = asyncio.Event()

    async def assess(self, context):
        self.started.set()
        await asyncio.Event().wait()


def action(call_id, target="bowl", **extra):
    return {"calls": [{"id": call_id, "name": "effect.run", "arguments": {"object": target}}], **extra}


def judgment(call_id, verdict="satisfied", **extra):
    return {"call_id": call_id, "verdict": verdict, "reason": "Compared actual final evidence",
            "evidence": [f"{call_id}:after:front"], **extra}


def finish(**extra):
    return {"final": {"status": "completed", "reason": "Task assessed"}, **extra}


class VerificationTests(unittest.IsolatedAsyncioTestCase):
    def config(self, verification=None):
        return AppConfig(model=AdapterSpec(adapter="primitive_vla.agent.model:JSONModel",
                                           options={"model": "fixture", "max_images": 3}),
            tool_providers=[AdapterSpec(adapter=__name__ + ":ReviewableTools")],
            verification=verification or AgentVerificationConfig(), system_prompt="Fixture instructions")

    async def exercise(self, replies, *, verification=None, reviewer_reply=None, snapshot=None):
        requests, planner_contexts = [], []
        replies = iter(replies)

        def respond(request):
            body = json.loads(request.content)
            payload = json.loads(body["messages"][1]["content"][0]["text"])
            requests.append((body, payload))
            if "tools" in payload:
                planner_contexts.append(payload)
                reply = next(replies)
            else:
                reply = reviewer_reply or {"verdict": "satisfied", "reason": "Final placement observed",
                                           "evidence": [payload["call"]["id"]]}
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})

        with TemporaryDirectory() as folder:
            log = EventLog()
            runtime = Runtime(self.config(verification), Path(folder), log, transport=httpx.MockTransport(respond))
            if snapshot:
                runtime.session.restore(snapshot)
            try:
                report = await runtime.run("Place the bowl, then pour from the cup")
                sent = next(r.sent for r in runtime.resources if isinstance(r, ReviewableTools))
                return report, runtime.snapshot(), sent, requests, planner_contexts, log.events
            finally:
                await runtime.close()

    async def test_main_agent_assesses_and_advances_in_the_same_request(self):
        report, snapshot, sent, requests, contexts, events = await self.exercise([
            action("bowl"), action("cup", "cup", assessments=[judgment("bowl")]),
            finish(assessments=[judgment("cup")])])
        self.assertEqual((report["status"], report["turns"], report["model_requests"]), ("completed", 3, 3))
        self.assertEqual(sent, ["bowl", "cup"])
        self.assertEqual(len(requests), 3)  # No separate assessment API or turn.
        self.assertTrue(all(c["verification_mode"] == "main_agent" for c in contexts))
        self.assertFalse(any(t["name"] == "session.assess" for t in contexts[0]["tools"]))
        self.assertEqual([o["source"] for o in snapshot["outcomes"]], ["main_agent", "main_agent"])
        self.assertEqual(contexts[-1]["outcomes"][0]["verdict"], "satisfied")
        self.assertFalse(any(e["event"] == "outcome_review_started" for e in events))
        self.assertIn("history", contexts[1])

    async def test_before_after_pair_survives_three_final_views_and_fresh_observation(self):
        _, _, _, requests, _, _ = await self.exercise([
            action("bowl"), {"calls": [{"name": "camera.observe", "id": "fresh"}]}, finish()])
        labels = [p["text"] for p in requests[-1][0]["messages"][1]["content"][1:] if p["type"] == "text"]
        self.assertEqual(len(labels), 3)
        self.assertIn("Observation source: bowl:before:front", labels)
        self.assertIn("Observation source: bowl:after:front", labels)
        self.assertIn("Observation source: fresh:front", labels)

    async def test_all_verdicts_allow_agent_chosen_retry_and_completion(self):
        for verdict in ("unknown", "satisfied", "unsatisfied"):
            with self.subTest(verdict=verdict):
                report, _, sent, _, _, _ = await self.exercise([
                    action("first"), action("retry", assessments=[judgment("first", verdict)]),
                    finish(assessments=[judgment("retry", verdict)])])
                self.assertEqual(report["status"], "completed")
                self.assertEqual(sent, ["bowl", "bowl"])

    async def test_assessments_are_optional_and_unused_reviewer_is_not_constructed(self):
        config = AgentVerificationConfig(reviewer=AdapterSpec(adapter=__name__ + ":BrokenReviewer"))
        report, snapshot, sent, requests, _, _ = await self.exercise([action("first"), finish()], verification=config)
        self.assertEqual(report["status"], "completed")
        self.assertEqual((sent, len(requests)), (["bowl"], 2))
        self.assertIsNone(snapshot["outcomes"][0]["source"])

    async def test_bad_references_repair_response_without_partial_judgments_or_actions(self):
        report, snapshot, sent, _, _, _ = await self.exercise([
            action("first"), action("second", "cup", assessments=[judgment("first"), judgment("imaginary")]),
            action("second", "cup", assessments=[judgment("first", evidence=["invented_image"])]),
            action("second", "cup", assessments=[judgment("first")]), finish()])
        self.assertEqual((report["status"], report["model_errors"]), ("completed", 2))
        self.assertEqual(sent, ["bowl", "cup"])
        self.assertEqual(snapshot["outcomes"][0]["revision"], 1)

    async def test_judgment_can_be_revised_without_replaying_action(self):
        report, snapshot, sent, _, contexts, _ = await self.exercise([
            action("first"), {"calls": [{"id": "fresh", "name": "camera.observe"}],
                              "assessments": [judgment("first", "unknown")]},
            finish(assessments=[judgment("first", evidence=["fresh"])])])
        self.assertEqual(report["status"], "completed")
        self.assertEqual(sent, ["bowl"])
        self.assertEqual(contexts[-1]["outcomes"][0]["verdict"], "unknown")
        self.assertEqual(snapshot["outcomes"][0]["revision"], 2)
        self.assertEqual([e["verdict"] for e in snapshot["history"] if e["kind"] == "outcome_review"],
                         ["unknown", "satisfied"])

    async def test_independent_mode_adds_only_configured_evidence_requests(self):
        config = AgentVerificationConfig(mode="independent", reviewer=AdapterSpec(
            adapter="primitive_vla.verification.model:JSONOutcomeReviewer", options={"model": "review-fixture", "max_images": 6}))
        report, snapshot, sent, requests, contexts, _ = await self.exercise([
            action("first", memory="Private fallible hypothesis"),
            action("retry"), finish()], verification=config, reviewer_reply={
                "verdict": "unknown", "reason": "No reliable judgment", "evidence": []})
        self.assertEqual((report["status"], len(requests), len(contexts)), ("completed", 5, 3))
        self.assertEqual(sent, ["bowl", "bowl"])  # Unknown never blocks dispatch.
        reviews = [(body, payload) for body, payload in requests if "tools" not in payload]
        self.assertEqual(len(reviews), 2)
        for body, payload in reviews:
            self.assertEqual(body["model"], "review-fixture")
            self.assertNotIn("Private fallible hypothesis", json.dumps(body))
            self.assertTrue({"history", "working_memory", "outcomes", "tools"}.isdisjoint(payload))
            self.assertIn("result", payload)
            self.assertTrue(any(":before:" in label for label in payload["image_sources"]))
            self.assertTrue(any(":after:" in label for label in payload["image_sources"]))
        self.assertTrue(all(o["source"] == "independent" for o in snapshot["outcomes"]))

    async def test_reviewer_failure_records_unknown_without_replaying_or_switching_modes(self):
        config = AgentVerificationConfig(mode="independent", reviewer=AdapterSpec(
            adapter="primitive_vla.verification.model:JSONOutcomeReviewer", options={"model": "fixture"}))
        report, snapshot, sent, requests, _, _ = await self.exercise([
            action("first"), finish()], verification=config, reviewer_reply={"malformed": True})
        self.assertEqual((report["status"], len(requests)), ("completed", 3))
        self.assertEqual(sent, ["bowl"])
        self.assertEqual(snapshot["outcomes"][0]["verdict"], "unknown")
        self.assertIn("unavailable", snapshot["outcomes"][0]["reason"])

    async def test_world_model_adapter_and_mode_switch_preserve_history_without_reexecution(self):
        _, first, _, _, _, _ = await self.exercise([action("first"), finish(assessments=[judgment("first")])])
        config = AgentVerificationConfig(mode="independent", reviewer=AdapterSpec(adapter=__name__ + ":WorldModelFixture"))
        report, second, sent, requests, contexts, _ = await self.exercise([
            action("second", "cup"), finish()], verification=config, snapshot=first)
        self.assertEqual((report["status"], len(requests)), ("completed", 2))
        self.assertEqual(sent, ["cup"])
        self.assertTrue(contexts[0]["outcomes"][0]["historical"])
        self.assertEqual([o["source"] for o in second["outcomes"]], ["main_agent", "independent"])
        _, third, sent, requests, _, _ = await self.exercise([finish()], snapshot=second)
        self.assertEqual((sent, len(requests)), ([], 1))
        self.assertEqual(len(third["outcomes"]), 2)

    async def test_optional_independent_reassessment_uses_new_evidence_without_motion(self):
        config = AgentVerificationConfig(mode="independent", reviewer=AdapterSpec(adapter=__name__ + ":WorldModelFixture"))
        report, snapshot, sent, _, _, _ = await self.exercise([
            action("first"), {"calls": [{"id": "fresh", "name": "camera.observe"}]},
            {"calls": [{"name": "session.assess", "arguments": {"call_id": "first", "evidence": ["fresh"]}}]},
            finish()], verification=config)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(sent, ["bowl"])
        self.assertEqual(snapshot["outcomes"][0]["revision"], 2)

    async def test_cancelled_review_preserves_execution_record_and_closes_reviewer(self):
        def respond(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(action("first"))}}]})
        config = AgentVerificationConfig(mode="independent", reviewer=AdapterSpec(adapter=__name__ + ":SlowReviewer"))
        with TemporaryDirectory() as folder:
            runtime = Runtime(self.config(config), Path(folder), EventLog(), transport=httpx.MockTransport(respond))
            await runtime.start()
            reviewer = runtime.verification.reviewer
            task = asyncio.create_task(runtime.run("Place bowl"))
            try:
                await asyncio.wait_for(reviewer.started.wait(), 2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(len(runtime.session.results), 1)
                self.assertIn("cancelled", runtime.snapshot()["outcomes"][0]["reason"])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await runtime.close()
            self.assertTrue(reviewer.closed)

    def test_independent_mode_requires_explicit_reviewer(self):
        with self.assertRaisesRegex(ValueError, "requires verification.reviewer"):
            AgentVerificationConfig(mode="independent")
