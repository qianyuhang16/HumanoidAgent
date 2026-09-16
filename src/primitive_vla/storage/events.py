"""Append-only task events with optional persistence sinks."""

import json
from pathlib import Path
from time import time


class EventLog:
    def __init__(self, path: Path | None = None, sink=None):
        self.path = path
        self.sink = sink
        self.events: list[dict] = []
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **data) -> None:
        record = {"time": time(), "event": event, **data}
        self.events.append(record)
        if self.sink:
            self.sink(record)
        if self.path:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
