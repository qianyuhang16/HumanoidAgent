import unittest

from primitive_vla.agent.memory import Session
from primitive_vla.state.contracts import Observation, StateClaim
from primitive_vla.state.journal import StateJournal
from primitive_vla.tools import ToolCall, ToolResult


class StateJournalTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.state = StateJournal(clock=lambda: self.now)

    def observe(self, ref, captured=100.0):
        self.state.record_result(ToolCall(id="call-" + ref, name="camera"), ToolResult(
            observations=[Observation(id=ref, sensor="camera", captured_at=captured)]))

    def claim(self, ref, value=True, source="model", **extra):
        return self.state.record_claims([StateClaim(subject="cup", property="holding.v1", value=value,
                                                   evidence=[ref], **extra)], source)[0]

    def test_late_old_result_does_not_overwrite_newer_state(self):
        self.observe("new", 99.0)
        current = self.claim("new")
        self.observe("old", 90.0)
        old = self.claim("old", False)
        self.assertEqual(self.state.query()[0]["id"], current)
        self.assertEqual(self.state.read(old)["payload"]["value"], False)

    def test_conflicts_keep_both_sources_and_ttl_expires(self):
        self.observe("obs")
        self.claim("obs", ttl_s=2.0)
        self.claim("obs", False, "sensor", ttl_s=2.0)
        self.assertEqual([r["validity"] for r in self.state.query()], ["conflict", "conflict"])
        self.now = 103.0
        self.assertEqual([r["validity"] for r in self.state.query()], ["stale", "stale"])

    def test_motion_and_restore_invalidate_without_erasing_evidence(self):
        self.observe("obs")
        claim = self.claim("obs")
        self.now = 101.0
        self.state.invalidate("move", "action")
        self.assertEqual(self.state.validity(claim), "stale")
        self.observe("late", 100.0)
        self.assertEqual(self.state.validity(self.claim("late")), "stale")
        self.state.restore(self.state.snapshot())
        self.assertEqual(self.state.validity(claim), "historical")

    def test_bad_contribution_is_atomic_and_receipt_does_not_invent_capture_time(self):
        result = ToolResult(observations=[Observation(id="obs", sensor="camera")], claims=[
            StateClaim(subject="cup", property="holding.v1", value=True, evidence=["missing"])])
        with self.assertRaises(ValueError):
            self.state.record_result(ToolCall(name="camera", id="call"), result)
        self.assertFalse(self.state.records)
        self.state.record_result(ToolCall(name="camera", id="call"), ToolResult(
            observations=[Observation(id="obs", sensor="camera")]))
        self.assertEqual(self.state.validity(self.claim("obs")), "unknown")

    def test_session_snapshot_and_legacy_restore_preserve_retrieval(self):
        session = Session()
        session.record_result(ToolCall(id="call", name="camera"), ToolResult(images={"front": "image.png"}))
        for snapshot in (session.snapshot(), {"history": session.events}):
            restored = Session()
            journal = restored.state
            restored.restore(snapshot)
            self.assertIs(restored.state, journal)
            self.assertEqual(restored.state.read("call:front")["payload"]["artifacts"]["image"], "image.png")
            self.assertEqual(restored.images, {})
            self.assertEqual(len(restored.results), 1)

    def test_reused_observation_keeps_capture_time_and_cannot_be_rewritten(self):
        obs = Observation(id="frame", sensor="front", captured_at=99.0, artifacts={"image": "frame.png"})
        for name in ("camera", "segment"):
            self.state.record_result(ToolCall(name=name, id=name), ToolResult(observations=[obs], images={"front": "frame.png"}))
        self.assertEqual(self.state.read("segment:front")["captured_at"], 99.0)
        self.assertEqual(self.state.read("frame")["source"], "camera")
        with self.assertRaisesRegex(ValueError, "different content"):
            self.state.record_result(ToolCall(name="segment", id="rewrite"), ToolResult(
                observations=[obs.model_copy(update={"captured_at": 100.0})]))
        self.assertNotIn("rewrite", self.state.records)
