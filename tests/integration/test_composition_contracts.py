import unittest

from primitive_vla.runtime.composition import load_adapter


class FakeModel:
    async def step(self, context, tools):
        raise NotImplementedError


class CompositionContractTests(unittest.TestCase):
    def test_paths_are_resolved_only_at_composition_root(self):
        self.assertIs(load_adapter(__name__ + ":FakeModel"), FakeModel)

    def test_invalid_adapter_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid adapter path"):
            load_adapter("missing_factory_separator")

    def test_unknown_factory_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "Cannot load adapter"):
            load_adapter(__name__ + ":MissingFactory")
