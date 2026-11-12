from collections.abc import Callable
import json

from primitive_vla.vlm import Function, ImageInput, VLM, VLMRequest
from .base import Decision, PlanningContext


class ModelPlanner:
    """One planning implementation for every VLM adapter."""

    def __init__(self, vlm: VLM, *, read_resource: Callable[[str], bytes] | None = None):
        self.vlm, self.read_resource = vlm, read_resource

    async def decide(self, context: PlanningContext) -> Decision:
        data = {key: value for key, value in context.data.items() if key != "instructions"}
        resource = data["last_result"]
        images = ()
        if resource.get("media_type") in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            if self.read_resource is None:
                raise ValueError("Image input requires a host-provided resource reader")
            images = (ImageInput(self.read_resource(resource["id"]), resource["media_type"]),)
        functions = tuple(Function(
            variant["properties"]["action"]["const"], variant["properties"]["arguments"],
        ) for variant in context.action_schema["oneOf"])
        result = await self.vlm.generate(VLMRequest(
            context.data["instructions"], json.dumps(data, ensure_ascii=False), functions, images,
        ))
        if len(result.calls) != 1:
            raise ValueError("Planning requires exactly one framework action per decision")
        call = result.calls[0]
        return Decision(call.name, call.arguments)
