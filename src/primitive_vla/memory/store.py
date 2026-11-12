from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path
from typing import Any
import asyncio
import json
import time

from primitive_vla.contracts import Domain, Failure, Record, ResourceRef, ToolError
from .files import leaf_id, parse, render, write_json, write_text


class Memory:
    """Markdown working memory and an independent immutable JSON execution journal.

    Single-process/event-loop ownership. Deleting a working document never removes
    the journal used for physical-control recovery. No database is used.
    """

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        if (self.directory / "memory.sqlite3").exists() and not (self.directory / "MEMORY.md").exists():
            raise ValueError("Legacy SQLite workspace: use python -m primitive_vla.memory.migrate "
                             "SOURCE DESTINATION before opening it with file memory")
        self.short = self.directory / "short"
        self.long = self.directory / "long"
        self.archive = self.directory / "archive"
        self.journal = self.directory / "journal"
        self.sessions = self.directory / "sessions"
        self.blobs = self.directory / "resources"
        self.references = self.blobs / "refs"
        for path in (self.short, self.long, self.archive, self.journal, self.sessions, self.references):
            path.mkdir(parents=True, exist_ok=True)
        self.rebuild_index()

    def write(self, record: Record) -> Record:
        leaf_id(record.id)
        if record.lifetime not in {"short", "long"}:
            raise ValueError("lifetime must be short or long")
        event_path = self.journal / f"{record.id}.json"
        if event_path.exists():
            raise ValueError(f"record already exists: {record.id}")
        body = render(record)
        for ref in record.resource_refs:
            if not (self.references / f"{leaf_id(ref.id)}.json").exists():
                self.register_resource(ref)
        # Journal first: loss of a working projection cannot lose a stop/finish fact.
        write_json(event_path, record.to_dict())
        if record.source == "planning" and record.event_type in {"decision", "action_result"}:
            return record  # Do not reintroduce forgotten notes through cached prompt/tool echoes.
        write_text(self.directory / record.lifetime / f"{record.id}.md", body)
        if record.lifetime == "long":
            self.rebuild_index()
        return record

    def _locate(self, record_id: str) -> Path:
        leaf_id(record_id)
        for directory in (self.short, self.long):
            path = directory / f"{record_id}.md"
            if path.exists():
                return path
        raise KeyError(record_id)

    def get(self, record_id: str) -> Record:
        record = parse(self._locate(record_id).read_text(encoding="utf-8"))
        if record.expires_at is not None and record.expires_at <= time.time():
            raise KeyError(record_id)
        return record

    def read(self, record_id: str, offset: int = 0, limit: int = 8000) -> dict:
        record = self.get(record_id)
        content = render(record)
        return {"id": record.id, "format": "markdown", "path": f"{record.lifetime}/{record.id}.md",
                "content": content[offset:offset + limit], "offset": offset, "total_chars": len(content)}

    def evidence(self, record_id: str, version: str | None = None) -> Record:
        record_id = leaf_id(record_id)
        path = (self.archive / f"{record_id}-{leaf_id(version)}.md" if version is not None
                else self.journal / f"{record_id}.json")
        if not path.exists():
            raise KeyError(record_id)
        if version is not None:
            return parse(path.read_text(encoding="utf-8"))
        return Record.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def history(self, session_id: str, limit: int = 60, offset: int = 0) -> list[Record]:
        records = [Record.from_dict(json.loads(path.read_text(encoding="utf-8")))
                   for path in self.journal.glob("*.json")]
        return sorted((record for record in records if record.session_id == session_id),
                      key=lambda record: (record.timestamp, record.id), reverse=True)[offset:offset + limit]

    def search(self, query: str = "", filters: dict[str, Any] | None = None,
               limit: int = 20, offset: int = 0) -> list[Record]:
        filters = filters or {}
        fields = {"session_id", "task_id", "call_id", "source", "domain", "event_type", "status", "lifetime"}
        if not filters.keys() <= fields or not 1 <= limit <= 100 or offset < 0:
            raise ValueError("invalid memory filters or page bounds")
        records = []
        now = time.time()
        for directory in (self.short, self.long):
            for path in directory.glob("*.md"):
                text = path.read_text(encoding="utf-8")
                record = parse(text)
                if record.expires_at is not None and record.expires_at <= now:
                    continue
                if all(getattr(record, key) == value for key, value in filters.items()) and all(
                    term in text.lower() for term in query.lower().split()
                ):
                    records.append(record)
        records.sort(key=lambda record: (record.timestamp, record.id), reverse=True)
        return records[offset:offset + limit]

    def update(self, record_id: str, changes: dict[str, Any], *, session_id: str | None = None) -> Record:
        if not changes.keys() <= {"payload", "status", "lifetime", "expires_at"}:
            raise ValueError("record identity and provenance are immutable")
        record = self.get(record_id)
        if (record.domain != Domain.PLANNING or record.source != "planning" or record.event_type != "note"
                or session_id is not None and record.session_id != session_id):
            raise ToolError(Failure.PERMISSION, "Planning may update its own notes, not Tool evidence")
        updated = replace(record, **changes)
        if updated.lifetime not in {"short", "long"}:
            raise ValueError("lifetime must be short or long")
        destination = self.directory / updated.lifetime / f"{record.id}.md"
        write_text(destination, render(updated))
        if record.lifetime != updated.lifetime:
            (self.directory / record.lifetime / f"{record.id}.md").unlink()
        self.rebuild_index()
        return updated

    def delete(self, record_id: str, *, session_id: str) -> dict:
        record = self.get(record_id)
        if record.lifetime != "short" or record.session_id != session_id:
            raise ToolError(Failure.PERMISSION, "only this session's short-term memory can be deleted")
        (self.short / f"{record.id}.md").unlink()
        return {"deleted": record.id, "journal_preserved": True}

    def rebuild_index(self) -> None:
        lines = ["# Long-term memory", "", "Read leaves with memory_get; search working records with memory_search.",
                 "Generated summaries are knowledge notes, not independent Verification.", ""]
        for path in sorted(self.long.glob("*.md")):
            record = parse(path.read_text(encoding="utf-8"))
            title = str(record.payload.get("title", f"{record.source}: {record.event_type}")).replace("\n", " ")
            lines.append(f"- [{title}](long/{record.id}.md) — `{record.id}` · {record.source}")
        write_text(self.directory / "MEMORY.md", "\n".join(lines) + "\n")

    def read_index(self, offset: int = 0, limit: int = 8000) -> dict:
        content = (self.directory / "MEMORY.md").read_text(encoding="utf-8")
        return {"path": "MEMORY.md", "content": content[offset:offset + limit],
                "offset": offset, "total_chars": len(content)}

    def put_resource(self, data: bytes, media_type: str, description: str = "") -> ResourceRef:
        digest = sha256(data).hexdigest()
        (self.blobs / digest).write_bytes(data)
        ref = ResourceRef(uri=f"blob:{digest}", media_type=media_type, description=description)
        self.register_resource(ref)
        return ref

    def register_resource(self, ref: ResourceRef) -> None:
        write_json(self.references / f"{leaf_id(ref.id)}.json", asdict(ref))

    def get_resource(self, resource_id: str) -> ResourceRef:
        path = self.references / f"{leaf_id(resource_id)}.json"
        if not path.exists():
            raise KeyError(resource_id)
        return ResourceRef(**json.loads(path.read_text(encoding="utf-8")))

    def read_resource(self, resource_id: str) -> bytes:
        ref = self.get_resource(resource_id)
        digest = ref.uri.removeprefix("blob:")
        if not ref.uri.startswith("blob:") or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise ValueError("external resources must be resolved by their Provider")
        return (self.blobs / digest).read_bytes()

    def cleanup(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        removed = 0
        for path in self.short.glob("*.md"):
            record = parse(path.read_text(encoding="utf-8"))
            if record.expires_at is not None and record.expires_at <= now:
                path.unlink()
                removed += 1
        return removed

    async def maintain(self, stop: asyncio.Event, interval: float = 60.0) -> None:
        """Rule-based cleanup for hosts without a model organizer."""
        if interval <= 0:
            raise ValueError("maintenance interval must be positive")
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), interval)
            except asyncio.TimeoutError:
                self.cleanup()
                self.rebuild_index()

    def save_session(self, session: dict[str, Any]) -> None:
        write_json(self.sessions / f"{leaf_id(session['id'])}.json", session)

    def unfinished_calls(self) -> list[Record]:
        records = [Record.from_dict(json.loads(path.read_text(encoding="utf-8")))
                   for path in self.journal.glob("*.json")]
        finished = {record.call_id for record in records if record.event_type in {"tool_finished", "verification"}}
        return sorted((record for record in records if record.event_type == "tool_started"
                       and record.call_id not in finished), key=lambda record: record.timestamp)

    def load_session(self, session_id: str) -> dict[str, Any]:
        path = self.sessions / f"{leaf_id(session_id)}.json"
        if not path.exists():
            raise KeyError(session_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def list_sessions(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("invalid session page bounds")
        sessions = [json.loads(path.read_text(encoding="utf-8")) for path in self.sessions.glob("*.json")]
        sessions.sort(key=lambda session: session["created_at"], reverse=True)
        return sessions[offset:offset + limit]

    def close(self) -> None:
        """Every mutation is persisted immediately; no database handle is held."""
