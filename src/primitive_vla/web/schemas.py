"""HTTP request payloads."""

from pydantic import BaseModel, Field


class TaskRequest(BaseModel):
    instruction: str = Field(default="", max_length=10000)
    task: dict | None = None
