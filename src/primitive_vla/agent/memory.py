"""Small in-process memory adapter for the canonical Agent."""

from .contracts import AgentEvent, MemorySnapshot


class InMemory:
    def __init__(self):
        self.events: list[AgentEvent] = []

    def read(self) -> MemorySnapshot:
        return MemorySnapshot({"events": tuple(self.events)})

    def update(self, event: AgentEvent) -> None:
        self.events.append(event)
