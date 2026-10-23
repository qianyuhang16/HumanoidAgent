"""Provider-specific encoding must preserve arbitrary tool arguments."""

import unittest

from jsonschema import Draft202012Validator

from primitive_vla.providers.codex_schema import normalize, output_schema


class ModelWireTests(unittest.TestCase):
    def test_codex_outcome_assessment_uses_the_same_three_state_contract(self):
        schema = output_schema('outcome_assessment', {})
        response = {'goal': 'placed', 'verdict': 'unknown', 'reason': 'Evidence missing', 'evidence': []}
        Draft202012Validator(schema).validate(response)
        self.assertEqual(normalize('outcome_assessment', response), response)

    def test_codex_wire_decodes_arguments_before_runtime_validation(self):
        schema = output_schema("tool_step", {"tools": [{"name": "custom.locate"}]})
        raw = {"calls": [{"name": "custom.locate", "arguments_json": '{"query":"yellow cup","views":[1,2]}'}],
               "final": None, "memory": None, "assessments": [], "claims": []}
        Draft202012Validator(schema).validate(raw)
        result = normalize("tool_step", raw)
        self.assertEqual(result["calls"][0]["arguments"], {"query": "yellow cup", "views": [1, 2]})

    def test_assessment_survives_normalization_alongside_next_action(self):
        raw = {"calls": [{"name": "act", "arguments_json": "{}"}], "final": None, "memory": None, "claims": [],
               "assessments": [{"call_id": "previous", "goal_id": None, "goal": None, "verdict": "satisfied",
                                "reason": "Placed", "evidence": ["previous:after:front"]}]}
        Draft202012Validator(output_schema("tool_step", {"tools": [{"name": "act"}]})).validate(raw)
        self.assertEqual(normalize("tool_step", raw)["assessments"][0]["call_id"], "previous")

    def test_state_values_and_goal_judgments_preserve_the_same_semantics(self):
        raw = {"calls": [], "final": {"status": "completed", "reason": "done", "evidence": []},
               "memory": None, "assessments": [{"call_id": None, "goal_id": "held", "goal": None,
                    "verdict": "satisfied", "reason": "seen", "evidence": ["obs"]}],
               "claims": [{"subject": "cup", "property": "holding.v1", "value_json": "true",
                    "status": "known", "basis": "inferred", "reason": "seen", "evidence": ["obs"]}]}
        Draft202012Validator(output_schema("tool_step", {"tools": []})).validate(raw)
        from primitive_vla.agent.contracts import ModelReply
        reply = ModelReply.model_validate(normalize("tool_step", raw))
        self.assertIs(reply.claims[0].value, True)
        self.assertEqual(reply.assessments[0].goal_id, "held")

    def test_codex_non_object_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize("tool_step", {"calls": [{"name": "custom", "arguments_json": "[]"}]})
