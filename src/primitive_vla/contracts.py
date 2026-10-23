"""Small wire foundation shared by the three embodied runtime responsibilities."""

from pydantic import BaseModel, ConfigDict


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
