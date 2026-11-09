import asyncio
from collections import defaultdict


class EventBus:
    """Coalesced wake signals. Evidence is durable in Memory, never in an unbounded queue."""

    def __init__(self):
        self._versions: dict[str, int] = defaultdict(int)
        self._signals: dict[str, asyncio.Event] = defaultdict(asyncio.Event)
        self._subscriptions: dict[str, set[str]] = defaultdict(set)

    def version(self, session_id: str) -> int:
        return self._versions[session_id]

    def subscribe(self, session_id: str, sources: list[str]) -> None:
        self._subscriptions[session_id] = set(sources)

    def interested(self, session_id: str, source: str) -> bool:
        return source in self._subscriptions[session_id]

    def wake(self, session_id: str) -> None:
        self._versions[session_id] += 1
        self._signals[session_id].set()

    async def wait(self, session_id: str, after: int, timeout: float | None = None) -> None:
        signal = self._signals[session_id]
        while self.version(session_id) <= after:
            signal.clear()
            await asyncio.wait_for(signal.wait(), timeout)
