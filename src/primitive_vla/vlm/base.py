from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Function:
    name: str
    parameters: dict[str, Any]
    description: str = ""


@dataclass(frozen=True)
class FunctionCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ImageInput:
    data: bytes
    media_type: str


@dataclass(frozen=True)
class VLMRequest:
    instructions: str
    text: str
    functions: tuple[Function, ...] = ()
    images: tuple[ImageInput, ...] = ()


@dataclass(frozen=True)
class VLMResponse:
    calls: tuple[FunctionCall, ...] = ()
    text: str | None = None


class VLM(Protocol):
    async def generate(self, request: VLMRequest) -> VLMResponse:
        """Generate text/functions; honor cancellation and propagate transport failures.

        Instructions and function schemas belong to the caller, never the model adapter.
        Adapters decode provider output; callers validate their own function contracts.
        """
        ...
