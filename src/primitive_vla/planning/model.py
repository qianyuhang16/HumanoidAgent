from collections.abc import Callable
from dataclasses import asdict, replace
import json

from primitive_vla.vlm import Function, ImageInput, VLM, VLMRequest
from .base import Decision, InvalidModelResponse, PlanningContext


class ModelPlanner:
    """One planning implementation for every VLM adapter."""

    def __init__(self, vlm: VLM, *, read_resource: Callable[[str], bytes] | None = None):
        self.vlm, self.read_resource = vlm, read_resource

    async def decide(self, context: PlanningContext) -> Decision:
        data = {key: value for key, value in context.data.items() if key != "instructions"}
        if context.image_resources and self.read_resource is None:
            raise ValueError("Image input requires a host-provided resource reader")
        resources = {ref["id"]: ref["media_type"] for ref in context.image_resources}
        messages = tuple(replace(message, images=tuple(ImageInput(self.read_resource(id), resources[id])
                                                       for id in ids)) if ids else message
                         for message, ids in context.messages)
        images = () if messages else tuple(ImageInput(self.read_resource(id), media_type)
                                            for id, media_type in resources.items())
        functions = (Function(
            "decide", {"type": "object", **context.action_schema},
            "Select exactly one next framework action and its arguments.",
        ),)
        result = await self.vlm.generate(VLMRequest(
            context.data["instructions"], "" if messages else json.dumps(data, ensure_ascii=False),
            functions, images, messages,
        ))
        if len(result.calls) != 1 or result.calls[0].name != "decide":
            raise InvalidModelResponse(
                'Return exactly one function call named decide with arguments '
                '{"action": "<framework action>", "arguments": {...}}. '
                'Framework action names belong inside action, not in the function name. '
                'No returned call was executed.',
                [asdict(call) for call in result.calls],
            )
        call = result.calls[0]
        return Decision(**call.arguments)
