import unittest

from primitive_vla.state.contracts import Observation, StateClaim
from primitive_vla.state.journal import StateJournal
from primitive_vla.tools import ToolCall, ToolResult
from primitive_vla.verification.contracts import GoalSpec, VerificationPolicy
from primitive_vla.verification.policy import GoalPolicy


class ReferenceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.state = StateJournal(clock=lambda: self.now)
        self.policy = GoalPolicy(self.state, VerificationPolicy(
            goals={"arrived": GoalSpec(description="Robot arrived", subject="rover", max_age_s=5.0)},
            completion=["arrived"]))

    def observe(self, ref, sensor="odometry", captured=100.0, **extra):
        self.state.record_result(ToolCall(id="call-" + ref, name="sensor.read"), ToolResult(observations=[
            Observation(id=ref, subject="rover", sensor=sensor, captured_at=captured, **extra)]))

    def claim(self, refs, source="localizer"):
        return self.state.record_claims([StateClaim(subject="rover", property="position.v1",
            value={"coordinates": [1, 2, 0], "frame": "map", "unit": "m"}, evidence=refs)], source)[0]

    def assess(self, refs):
        self.state.record_assessment({"goal_id": "arrived", "verdict": "satisfied", "evidence": refs}, "reviewer")
        return self.policy.check(["arrived"])

    def move(self):
        call = {"id": "move", "name": "navigate", "arguments": {}}
        self.state.record_execution("tool_started", call, read_only=False)
        self.state.invalidate("moving", "move")
        self.now += 1
        self.state.record_execution("tool_finished", call, result={"control_released": True})

    def test_reference_survives_motion_while_scene_and_derived_claims_expire(self):
        self.observe("map", "map", 50.0, lifetime="reference", ttl_s=60.0,
                     artifacts={"map": {"kind": "image", "uri": "map.png"}})
        self.observe("pose")
        scene_claim = self.claim(["pose", "map"])
        reference_claim = self.claim(["map"], "map-reader")
        self.move()
        self.assertEqual(self.state.validity("map", max_age_s=5.0), "current")
        self.assertEqual(self.state.validity("call-map:map:map"), "current")
        self.assertEqual(self.state.validity(reference_claim), "current")
        self.assertEqual(self.state.validity("pose"), "stale")
        self.assertEqual(self.state.validity(scene_claim), "stale")
        self.now = 111.0
        self.assertEqual(self.state.validity(reference_claim), "stale")

    def test_reference_alone_and_nested_claims_do_not_certify_arrival(self):
        self.observe("map", "map", lifetime="reference", artifacts={"map": {"kind": "image", "uri": "map.png"}})
        first = self.claim(["map"])
        nested = self.claim([first], "model")
        for refs in (["map"], ["call-map:map:map"], [first], [nested]):
            self.assertIsNotNone(self.assess(refs))

    def test_fresh_localization_can_use_old_map_without_losing_measurement_time(self):
        self.observe("map", "map", 50.0, lifetime="reference", ttl_s=120.0)
        self.move()
        self.observe("pose", captured=self.now)
        localized = self.claim(["map", "pose"])
        self.assertEqual(self.state.read(localized)["captured_at"], 101.0)
        self.assertEqual(self.state.read(localized)["evidence"], ["map", "pose"])
        self.assertIsNone(self.assess([localized]))
        self.assertIsNone(self.assess(["map", "pose"]))
        self.now = 107.0
        self.assertIsNotNone(self.policy.check(["arrived"]))

    def test_map_replacement_invalidates_dependent_claim_and_restore_is_historical(self):
        self.observe("map-v1", "map", 50.0, lifetime="reference")
        self.observe("pose")
        localized = self.claim(["map-v1", "pose"])
        self.assertIsNone(self.assess([localized]))
        self.observe("map-v2", "map", 99.0, lifetime="reference")
        self.assertEqual(self.state.validity(localized), "stale")
        self.assertIsNotNone(self.policy.check(["arrived"]))
        self.assertEqual(self.state.observations(sensor="map")[0]["id"], "map-v2")
        self.state.restore(self.state.snapshot())
        self.assertEqual(self.state.validity("map-v2"), "historical")

    def test_unknown_or_expired_reference_is_not_hidden_by_fresh_measurement(self):
        for captured, ttl in ((None, None), (50.0, 2.0)):
            self.setUp()
            self.observe("map", "map", captured, lifetime="reference", ttl_s=ttl)
            self.observe("pose")
            localized = self.claim(["map", "pose"])
            self.assertNotEqual(self.state.validity(localized), "current")
            self.assertIsNotNone(self.assess([localized]))
