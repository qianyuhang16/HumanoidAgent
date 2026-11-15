"""API-key deployment settings, independent of robot/Skill assembly."""
from dataclasses import dataclass, field
import os

from .base import VLM


@dataclass(frozen=True)
class VLMConfig:
    provider: str
    model: str
    api_key: str = field(default="", repr=False)
    api_key_env: str | None = None
    base_url: str | None = None
    timeout: float = 60
    max_tokens: int = 2048

    def connect(self) -> VLM:
        if self.provider == "deepseek":
            from .deepseek import DeepSeekVLM
            adapter, key_env = DeepSeekVLM, "DEEPSEEK_API_KEY"
        elif self.provider == "gpt":
            from .gpt import GPTVLM
            adapter, key_env = GPTVLM, "OPENAI_API_KEY"
        else:
            raise ValueError(f"unsupported VLM provider: {self.provider}")
        key = os.environ.get(self.api_key_env or key_env) or self.api_key
        options = {"model": self.model, "timeout": self.timeout, "max_tokens": self.max_tokens}
        if self.base_url is not None:
            options["base_url"] = self.base_url
        return adapter(key, **options)
