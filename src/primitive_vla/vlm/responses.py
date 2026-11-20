"""Responses wire format shared by adapters that support multimodal tool results."""

import base64
from dataclasses import asdict
import json

import httpx

from .base import FunctionCall, Message, VLMRequest, VLMResponse


async def generate_response(request: VLMRequest, *, url: str, api_key: str, model: str,
                            timeout: float, max_tokens: int, transport, options: dict) -> VLMResponse:
    items = []
    for message in request.messages or (Message("user", request.text, request.images),):
        if message.call:
            items.append({"type": "function_call", "call_id": message.call_id, "name": message.call.name,
                          "arguments": json.dumps(message.call.arguments, ensure_ascii=False)})
            continue
        text_type = "output_text" if message.role == "assistant" else "input_text"
        content = [{"type": text_type, "text": message.text}]
        for image in message.images:
            encoded = base64.b64encode(image.data).decode("ascii")
            content.append({"type": "input_image", "detail": "auto",
                            "image_url": f"data:{image.media_type};base64,{encoded}"})
        if message.role == "tool":
            items.append({"type": "function_call_output", "call_id": message.call_id,
                          "output": content if message.images else message.text})
        else:
            items.append({"role": message.role, "content": content})

    body = {"model": model, "instructions": request.instructions, "input": items,
            "max_output_tokens": max_tokens, "store": False, "stream": False, **options}
    if request.functions:
        body.update(tools=[{"type": "function", **asdict(function), "strict": False}
                           for function in request.functions], tool_choice="required",
                    parallel_tool_calls=False)
    async with httpx.AsyncClient(timeout=timeout, transport=transport, trust_env=False) as client:
        response = await client.post(f"{url}/responses", headers={"Authorization": f"Bearer {api_key}"},
                                     json=body)
        response.raise_for_status()
    result = response.json()
    if result["status"] != "completed":
        raise ValueError(f"Responses generation was incomplete: {result['status']}")
    calls, text = [], []
    for item in result["output"]:
        if item["type"] == "function_call":
            calls.append(FunctionCall(item["name"], json.loads(item["arguments"])))
        elif item["type"] == "message":
            for part in item["content"]:
                if part["type"] == "refusal":
                    raise ValueError("Model refused the request")
                if part["type"] == "output_text":
                    text.append(part["text"])
    return VLMResponse(tuple(calls), "\n".join(text) if text else None)
