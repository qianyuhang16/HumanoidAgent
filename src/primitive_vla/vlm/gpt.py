"""OpenAI Responses wire protocol; prompts and execution remain with the caller."""
import base64
from dataclasses import asdict
import json

import httpx

from .base import FunctionCall, VLMRequest, VLMResponse


class GPTVLM:
    def __init__(self, api_key: str, *, model: str,
                 base_url: str = "https://api.openai.com/v1", timeout: float = 60,
                 max_tokens: int = 2048, transport: httpx.AsyncBaseTransport | None = None):
        if not api_key.strip() or not model.strip() or timeout <= 0 or max_tokens <= 0:
            raise ValueError("GPT requires an API key, model, and positive request budgets")
        self._api_key = api_key
        self.model, self.base_url = model, base_url.rstrip("/")
        self.timeout, self.max_tokens, self.transport = timeout, max_tokens, transport

    async def generate(self, request: VLMRequest) -> VLMResponse:
        content = [{"type": "input_text", "text": request.text}]
        for image in request.images:
            encoded = base64.b64encode(image.data).decode("ascii")
            content.append({"type": "input_image", "detail": "auto",
                            "image_url": f"data:{image.media_type};base64,{encoded}"})
        body = {"model": self.model, "instructions": request.instructions,
                "input": [{"role": "user", "content": content}],
                "max_output_tokens": self.max_tokens, "store": False, "stream": False}
        if request.functions:
            # Preserve optional fields in the caller's schema. Runtime validates arguments.
            body.update(tools=[{"type": "function", **asdict(function), "strict": False}
                               for function in request.functions], tool_choice="required",
                        parallel_tool_calls=False)
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport,
                                     trust_env=False) as client:
            response = await client.post(f"{self.base_url}/responses", headers={
                "Authorization": f"Bearer {self._api_key}",
            }, json=body)
            response.raise_for_status()
        result = response.json()
        if result["status"] != "completed":
            raise ValueError(f"GPT generation was incomplete: {result['status']}")
        calls, text = [], []
        for item in result["output"]:
            if item["type"] == "function_call":
                calls.append(FunctionCall(item["name"], json.loads(item["arguments"])))
            elif item["type"] == "message":
                for part in item["content"]:
                    if part["type"] == "refusal":
                        raise ValueError("GPT refused the request")
                    if part["type"] == "output_text":
                        text.append(part["text"])
        return VLMResponse(tuple(calls), "\n".join(text) if text else None)
