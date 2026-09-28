"""Configuration for the generic Agent, model transports and tool plugins."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodexConfig(Section):
    executable: str = "codex"
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "medium"


class VLMConfig(Section):
    provider: Literal["http", "codex"] = "http"
    codex: CodexConfig = Field(default_factory=CodexConfig)
    base_url: str = "http://127.0.0.1:8000/v1"
    model: str = ""
    api_key_env: str = "VLM_API_KEY"
    require_api_key: bool = False
    temperature: float = Field(default=0.1, ge=0, le=2)
    max_tokens: int = Field(default=2048, gt=0)
    timeout_s: float = Field(default=45, gt=0)
    json_mode: bool = True
    max_images: int = Field(default=4, ge=0, le=16)
    max_image_bytes: int = Field(default=8_388_608, gt=0)
    image_detail: Literal["auto", "low", "high"] = "auto"


class WebConfig(Section):
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    refresh_ms: int = Field(default=700, ge=100)


class AdapterSpec(Section):
    """Every plugin is constructed as factory(options, PluginContext)."""
    adapter: str = Field(pattern=r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
    options: dict = Field(default_factory=dict)


class AgentLimits(Section):
    max_turns: int = Field(default=40, gt=0)
    max_model_retries: int = Field(default=2, ge=0)
    max_calls: int = Field(default=80, gt=0)
    timeout_s: float = Field(default=180, gt=0)
    stop_timeout_s: float = Field(default=2, gt=0)
    history_limit: int = Field(default=20, ge=2)
    result_chars: int = Field(default=12000, ge=256)
    memory_chars: int = Field(default=12000, ge=256)
    denied_tools: list[str] = Field(default_factory=list)


class AgentVerificationConfig(Section):
    mode: Literal["main_agent", "independent"] = "main_agent"
    reviewer: AdapterSpec | None = None

    @model_validator(mode="after")
    def independent_reviewer(self):
        if self.mode == "independent" and self.reviewer is None:
            raise ValueError("verification.mode=independent requires verification.reviewer")
        return self


class AppConfig(Section):
    """The tool Agent config has no required scene, robot, policy or goal vocabulary."""
    schema_version: Literal[2] = 2
    name: str = "Primitive · Tool Agent"
    model: AdapterSpec = Field(default_factory=lambda: AdapterSpec(
        adapter="primitive_vla.agent.model:JSONModel", options={"model": ""}))
    tool_providers: list[AdapterSpec] = Field(default_factory=list)
    tools: dict[str, AdapterSpec] = Field(default_factory=dict)
    instructions: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    limits: AgentLimits = Field(default_factory=AgentLimits)
    finish_check: AdapterSpec | None = None
    verification: AgentVerificationConfig = Field(default_factory=AgentVerificationConfig)
    web: WebConfig = Field(default_factory=WebConfig)
