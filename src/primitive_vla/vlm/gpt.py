"""OpenAI Responses wire protocol; prompts and execution remain with the caller."""
import httpx

from .base import VLMRequest, VLMResponse
from .responses import generate_response


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
        return await generate_response(request, url=self.base_url, api_key=self._api_key, model=self.model,
                                       timeout=self.timeout, max_tokens=self.max_tokens,
                                       transport=self.transport, options={})
