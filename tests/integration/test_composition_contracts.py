import unittest

from primitive_vla.runtime.composition import load_adapter, validate_adapter


class FakeDecision:
    async def decide(self, context):
        raise NotImplementedError


class CompositionContractTests(unittest.TestCase):
    def test_paths_are_resolved_only_at_composition_root(self):
        self.assertIs(load_adapter("tests.integration.test_composition_contracts:FakeDecision"), FakeDecision)

    def test_contract_validation_rejects_incomplete_adapter(self):
        with self.assertRaises(TypeError):
            validate_adapter("tests.integration.test_composition_contracts:FakeDecision", ("decide", "reset"))

    def test_contract_validation_accepts_decision_model(self):
        validate_adapter("tests.integration.test_composition_contracts:FakeDecision", ("decide",))
