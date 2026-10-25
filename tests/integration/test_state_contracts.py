import unittest

from primitive_vla.state.contracts import StateClaim
from primitive_vla.state.schema import StateSchemas
from primitive_vla.tools import ToolResult


class StateContractTests(unittest.TestCase):
    def test_geometry_needs_frame_and_units(self):
        schemas = StateSchemas()
        claim = StateClaim(subject="cup", property="position.v1", evidence=["frame"],
                           value={"coordinates": [1, 2, 3]})
        with self.assertRaises(Exception):
            schemas.validate(claim)
        claim.value.update(frame="robot", unit="m")
        schemas.validate(claim)

    def test_unknown_and_missing_evidence_are_not_known_values(self):
        for extra in ({"value": True}, {"status": "unknown", "value": True}):
            with self.assertRaises(ValueError):
                StateClaim(subject="cup", property="holding.v1", **extra)
        StateSchemas().validate(StateClaim(subject="cup", property="holding.v1", status="unknown"))

    def test_progress_requires_metric_and_extensions_are_explicit(self):
        schemas = StateSchemas()
        claim = StateClaim(subject="task", property="progress.v1", evidence=["frame"], value={"value": .9})
        with self.assertRaises(Exception):
            schemas.validate(claim)
        claim.value["metric"] = "trajectory_fraction"
        schemas.validate(claim)
        extension = StateClaim(subject="cup", property="temperature.v1", evidence=["sensor"], value=25)
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            schemas.validate(extension)
        schemas.register("temperature.v1", {"type": "number"})
        schemas.validate(extension)

    def test_legacy_tool_result_still_loads(self):
        result = ToolResult.model_validate({"data": {"arbitrary": "legacy payload"}})
        self.assertEqual(result.observations, [])
        self.assertEqual(result.claims, [])

    def test_known_null_is_decided_by_property_schema_not_envelope(self):
        schemas = StateSchemas()
        schemas.register("optional_label.v1", {"type": ["string", "null"]})
        claim = StateClaim(subject="cup", property="optional_label.v1", value=None,
                           evidence=["obs"], reason=None)
        schemas.validate(claim)
        with self.assertRaises(ValueError):
            schemas.validate(claim.model_copy(update={"property": "holding.v1"}))
        with self.assertRaises(ValueError):
            StateClaim(subject="cup", property="optional_label.v1", evidence=["obs"])

    def test_partial_values_obey_declared_schema_and_keep_null_fields(self):
        schemas = StateSchemas()
        schemas.register("pose_estimate.v1", {"type": "object", "properties": {
            "center": {"type": "array", "items": {"type": ["number", "null"]}},
            "frame": {"type": ["string", "null"]}}, "additionalProperties": False})
        claim = StateClaim(subject="cup", property="pose_estimate.v1", status="partial",
                           value={"center": [0, None, 1], "frame": None}, evidence=["obs"])
        schemas.validate(claim)
        self.assertIsNone(claim.model_dump()["value"]["center"][1])
        with self.assertRaises(ValueError):
            schemas.validate(claim.model_copy(update={"property": "position.v1"}))
        with self.assertRaises(ValueError):
            StateClaim(subject="cup", property="pose_estimate.v1", status="partial", value=None, evidence=["obs"])
