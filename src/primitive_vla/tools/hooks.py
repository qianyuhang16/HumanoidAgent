from __future__ import annotations

from collections import defaultdict
from typing import Awaitable, Callable

from primitive_vla.contracts import Domain, Record
from primitive_vla.events import EventBus
from primitive_vla.memory import Memory

Hook = Callable[[str, Record], Awaitable[None]]


class Hooks:
    """Memory is the journal; custom hooks extend individual domains."""

    def __init__(self, memory: Memory, events: EventBus):
        self.memory = memory
        self.events = events
        self._hooks: dict[Domain, list[Hook]] = defaultdict(list)

    def add(self, domain: Domain, hook: Hook) -> None:
        self._hooks[domain].append(hook)

    async def emit(self, stage: str, record: Record, wake: bool = True) -> None:
        self.memory.write(record)
        if wake:
            self.events.wake(record.session_id)
        for hook in self._hooks[record.domain]:
            try:
                await hook(stage, record)
            except Exception as error:
                # Extension boundary: a pre-call hook may veto, observers cannot rewrite outcomes.
                if stage == "before_call":
                    raise
                self.memory.write(Record(
                    session_id=record.session_id, task_id=record.task_id, call_id=record.call_id,
                    source="runtime.hooks", domain=record.domain, event_type="hook_error",
                    status="failed", payload={"stage": stage, "error": str(error)},
                ))
