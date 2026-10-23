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
