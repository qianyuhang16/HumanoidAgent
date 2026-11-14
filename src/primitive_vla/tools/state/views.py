"""Optional, read-only robot views. Preview frames are not task evidence."""
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Protocol


@dataclass(frozen=True)
class ViewSpec:
    id: str
    label: str
    is_default: bool = False


@dataclass(frozen=True)
class ViewFrame:
    id: str
    captured_at: float
    media_type: str
    data: bytes
    width: int
    height: int
    metadata: dict[str, Any] = field(default_factory=dict)


class RobotViewSource(Protocol):
    def list_views(self) -> tuple[ViewSpec, ...]:
        """Return unique view IDs and exactly one default when views are available."""
        ...

    def subscribe_view(self, view_id: str) -> AsyncGenerator[ViewFrame, None]:
        """Yield current frames, release the subscription in finally, and surface errors.

        Consumers may skip intermediate frames. Subscriptions must not move the robot,
        step a simulator, invoke Planning, or persist every frame as task evidence.
        """
        ...
