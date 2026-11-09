from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from primitive_vla.contracts import Domain, Failure, ToolError
from primitive_vla.providers.base import ToolProvider
from primitive_vla.tools.contracts import ToolSpec


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    provider: ToolProvider
    input_validator: Draft202012Validator
    output_validator: Draft202012Validator


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, spec: ToolSpec, provider: ToolProvider) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool: {spec.name}")
        if (not spec.name or not spec.provider or spec.domain not in
            {Domain.STATE, Domain.EXECUTION, Domain.VERIFICATION} or
            spec.execution_mode not in {"sync", "async"} or spec.timeout <= 0):
            raise ValueError("invalid ToolSpec")
        if spec.required_resources and spec.domain != Domain.EXECUTION:
            raise ValueError("control resources belong to execution tools")
        for schema in (spec.input_schema, spec.output_schema):
            Draft202012Validator.check_schema(schema)
        self._tools[spec.name] = RegisteredTool(
            spec, provider, Draft202012Validator(spec.input_schema),
            Draft202012Validator(spec.output_schema),
        )

    def get(self, name: str) -> RegisteredTool:
        if name not in self._tools:
            raise ToolError(Failure.INTERFACE, f"unknown tool: {name}")
        return self._tools[name]

    def catalog(self) -> list[ToolSpec]:
        """Read-only capability descriptions for hosting interfaces."""
        return [entry.spec for entry in self._tools.values()]

    def discover(self, robot_type: str, allowed_tools: tuple[str, ...] | None = None,
                 query: str = "", offset: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        tools = [entry.spec for entry in self._tools.values()
                 if (not entry.spec.supported_robots or robot_type in entry.spec.supported_robots)
                 and (allowed_tools is None or entry.spec.name in allowed_tools)
                 and query.casefold() in (entry.spec.name + " " + entry.spec.description).casefold()]
        return [{"name": spec.name, "domain": spec.domain.value,
                 "description": spec.description, "skill_ref": spec.skill_ref}
                for spec in tools[offset:offset + limit]]
