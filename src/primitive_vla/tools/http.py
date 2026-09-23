"""Transport for external model/robot tool servers with a small explicit protocol."""

import os
from time import monotonic, time

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .contracts import FunctionTool, ToolDefinition, ToolResult


class HTTPOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: HttpUrl
    api_key_env: str = "TOOL_API_KEY"
    tools: list[ToolDefinition] = Field(min_length=1)


class HTTPTools:
    """POST /call and /stop. The server must fence call IDs after stopping."""

    def __init__(self, options, context):
        cfg = HTTPOptions.model_validate(options)
        key = os.environ.get(cfg.api_key_env)
        self.endpoint = str(cfg.endpoint).rstrip("/")
        self.http = httpx.AsyncClient(transport=context.transport,
                                     headers={"Authorization": f"Bearer {key}"} if key else {})
        self.tools = []
        for definition in cfg.tools:
            async def call(args, context, name=definition.name):
                context.check_active()
                response = await self.http.post(self.endpoint + "/call", json={
                    "call_id": context.call_id, "tool": name, "arguments": args,
                    "deadline": time() + max(0, context.deadline - monotonic())},
                    timeout=max(.001, context.deadline - monotonic()))
                response.raise_for_status()
                return ToolResult.model_validate(response.json())
            self.tools.append(FunctionTool(definition, call, self.stop))

    async def stop(self, call_id, reason):
        response = await self.http.post(self.endpoint + "/stop", json={"call_id": call_id, "reason": reason})
        response.raise_for_status()
        result = response.json()
        return result.get("acknowledged") is True and result.get("call_id") == call_id

    async def close(self):
        await self.http.aclose()
