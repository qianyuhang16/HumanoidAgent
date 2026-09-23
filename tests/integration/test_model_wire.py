"""Provider-specific encoding must preserve arbitrary tool arguments."""

import unittest

from jsonschema import Draft202012Validator

from primitive_vla.providers.codex_schema import normalize, output_schema


class ModelWireTests(unittest.TestCase):
    def test_codex_wire_decodes_arguments_before_runtime_validation(self):
        schema = output_schema("tool_step", {"tools": [{"name": "custom.locate"}]})
        raw = {"calls": [{"name": "custom.locate", "arguments_json": '{"query":"yellow cup","views":[1,2]}'}],
               "final": None, "memory": None}
        Draft202012Validator(schema).validate(raw)
        result = normalize("tool_step", raw)
        self.assertEqual(result["calls"][0]["arguments"], {"query": "yellow cup", "views": [1, 2]})

    def test_codex_non_object_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize("tool_step", {"calls": [{"name": "custom", "arguments_json": "[]"}]})
