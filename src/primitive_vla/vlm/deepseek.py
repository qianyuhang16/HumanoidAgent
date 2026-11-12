"""DeepSeek wire protocol only: no Agent prompt, Memory, or robot policy."""
import base64
from dataclasses import asdict
import json

import httpx

from .base import FunctionCall, VLMRequest, VLMResponse


class DeepSeekVLM:
    def __init__(self, api_key: str, *, model: str = "deepseek-flash",
                 base_url: str = "https://api.deepseek.com", timeout: float = 60,
                 max_tokens: int = 2048, transport: httpx.AsyncBaseTransport | None = None):
        if not api_key.strip() or not model.strip() or timeout <= 0 or max_tokens <= 0:
            raise ValueError("DeepSeek requires an API key, model, and positive request budgets")
        self._api_key = api_key
        self.model, self.base_url = model, base_url.rstrip("/")
        self.timeout, self.max_tokens, self.transport = timeout, max_tokens, transport

    async def generate(self, request: VLMRequest) -> VLMResponse:
        content = [{"type": "text", "text": request.text}]
        for image in request.images:
            if len(image.data) > 32 * 1024 * 1024:
                raise ValueError("DeepSeek inline images must not exceed 32 MiB")
            encoded = base64.b64encode(image.data).decode("ascii")
            content.append({"type": "image_url", "image_url": {
                "url": f"data:{image.media_type};base64,{encoded}", "detail": "original"}})
        body = {
            "model": self.model, "messages": [
                {"role": "system", "content": request.instructions},
                {"role": "user", "content": content},
            ], "thinking": {"type": "disabled"}, "max_tokens": self.max_tokens, "stream": False,
        }
        if request.functions:
            body.update(tools=[{"type": "function", "function": asdict(function)}
                               for function in request.functions], tool_choice="required")
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport,
                                     trust_env=False) as client:
            response = await client.post(f"{self.base_url}/chat/completions", headers={
                "Authorization": f"Bearer {self._api_key}",
            }, json=body)
            response.raise_for_status()
        choice = response.json()["choices"][0]
        if choice["finish_reason"] not in {"tool_calls", "stop"}:
            raise ValueError(f"DeepSeek generation was incomplete: {choice['finish_reason']}")
        message = choice["message"]
        calls = tuple(FunctionCall(call["function"]["name"], json.loads(call["function"]["arguments"]))
                      for call in (message.get("tool_calls") or ()))
        return VLMResponse(calls, message.get("content"))
