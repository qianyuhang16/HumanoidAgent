"""Model-independent generation interface; importing it loads no vendor SDK."""
from .base import Function, FunctionCall, ImageInput, Message, VLM, VLMRequest, VLMResponse

__all__ = ["Function", "FunctionCall", "ImageInput", "Message", "VLM", "VLMRequest", "VLMResponse"]
