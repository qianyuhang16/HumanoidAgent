from dataclasses import dataclass
from typing import Any
import time

from primitive_vla.contracts import Domain, Record, ResourceRef
from primitive_vla.tools.hooks import Hooks


@dataclass
class ObservationSink:
    """Push boundary for streams. Providers own sensors, rates, and significance detection."""

    hooks: Hooks

    async def publish(self, *, session_id: str, task_id: str, source: str,
                      payload: dict[str, Any], resource_refs: tuple[ResourceRef, ...] = (),
                      significant: bool = False, ttl: float = 60.0) -> Record:
        if ttl <= 0:
            raise ValueError("observation TTL must be positive")
        record = Record(
            session_id=session_id, task_id=task_id, source=source, domain=Domain.STATE,
            event_type="observation", status="observed", payload=payload,
            resource_refs=resource_refs, expires_at=time.time() + ttl,
        )
        wake = significant or self.hooks.events.interested(session_id, source)
        await self.hooks.emit("on_progress", record, wake=wake)
        return record
