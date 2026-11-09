from typing import Any, Protocol

from primitive_vla.tools.contracts import CallContext, ToolResult


class ToolProvider(Protocol):
    async def execute(self, arguments: dict[str, Any], context: CallContext) -> ToolResult:
        """Return only after the underlying operation has ended, including physical control."""
        ...

    async def cancel(self, context: CallContext) -> bool:
        """True confirms physical stop; False means stop is not yet confirmed.

        Implementations must be idempotent. Cancelling a Python coroutine alone is not a stop.
        """
        ...
