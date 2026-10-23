"""Versioned property meanings; extensions register only the contracts they provide."""

from copy import deepcopy

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry


BUILTINS = {
    "holding.v1": {"type": "boolean"},
    "position.v1": {
        "type": "object", "properties": {
            "coordinates": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
            "frame": {"type": "string", "minLength": 1}, "unit": {"enum": ["m", "mm"]}},
        "required": ["coordinates", "frame", "unit"], "additionalProperties": False},
    "relation.v1": {
        "type": "object", "properties": {
            "relation": {"type": "string", "minLength": 1}, "object": {"type": "string", "minLength": 1}},
        "required": ["relation", "object"], "additionalProperties": False},
    "progress.v1": {
        "type": "object", "properties": {
            "value": {"type": "number"}, "metric": {"type": "string", "minLength": 1}},
        "required": ["value", "metric"], "additionalProperties": False},
    "detection.v1": {
        "type": "object", "properties": {
            "label": {"type": "string", "minLength": 1},
            "box_xyxy": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
            "frame": {"type": "string", "minLength": 1}, "unit": {"enum": ["pixel"]}},
        "required": ["label", "box_xyxy", "frame", "unit"], "additionalProperties": False},
}


class StateSchemas:
    def __init__(self):
        self.schemas = {}
        self.validators = {}
        for name, schema in BUILTINS.items():
            self.register(name, schema)

    def register(self, name, schema):
        if name in self.schemas:
            raise ValueError(f"Duplicate state contract: {name}")
        Draft202012Validator.check_schema(schema)
        self.schemas[name] = deepcopy(schema)
        self.validators[name] = Draft202012Validator(self.schemas[name], registry=Registry())

    def validate(self, claim):
        if claim.property not in self.validators:
            raise ValueError(f"Unsupported state contract: {claim.property}")
        if claim.status == "known":
            try:
                self.validators[claim.property].validate(claim.value)
            except ValidationError as error:
                raise ValueError(f"{claim.property}: {error.message}") from error
