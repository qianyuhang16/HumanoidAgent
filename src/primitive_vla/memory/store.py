from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import sqlite3
import time
import asyncio

from primitive_vla.contracts import Record, ResourceRef


class Memory:
    """SQLite journal plus content-addressed blobs; single event-loop ownership."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.blobs = self.directory / "resources"
        self.blobs.mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.directory / "memory.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY, timestamp REAL NOT NULL, session_id TEXT NOT NULL,
                task_id TEXT NOT NULL, call_id TEXT, source TEXT NOT NULL, domain TEXT NOT NULL,
                event_type TEXT NOT NULL, status TEXT NOT NULL, lifetime TEXT NOT NULL,
                expires_at REAL, body TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS records_session ON records(session_id, timestamp);
            CREATE INDEX IF NOT EXISTS records_call ON records(call_id);
            CREATE INDEX IF NOT EXISTS records_lifetime ON records(lifetime, expires_at);
            CREATE TABLE IF NOT EXISTS resources (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, body TEXT NOT NULL);
        """)

    def write(self, record: Record) -> Record:
        if record.lifetime not in {"short", "long"}:
            raise ValueError("lifetime must be short or long")
        body = json.dumps(record.to_dict(), ensure_ascii=False, allow_nan=False)
        with self.db:
            self.db.execute(
                "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record.id, record.timestamp, record.session_id, record.task_id, record.call_id,
                 record.source, record.domain.value, record.event_type, record.status,
                 record.lifetime, record.expires_at, body),
            )
            for ref in record.resource_refs:
                self.db.execute("INSERT OR IGNORE INTO resources VALUES (?, ?)",
                                (ref.id, json.dumps(asdict(ref), ensure_ascii=False)))
        return record

    def get(self, record_id: str) -> Record:
        row = self.db.execute("SELECT body FROM records WHERE id = ? AND "
                              "(expires_at IS NULL OR expires_at > ?)",
                              (record_id, time.time())).fetchone()
        if row is None:
            raise KeyError(record_id)
        return Record.from_dict(json.loads(row[0]))

    def search(self, query: str = "", filters: dict[str, Any] | None = None,
               limit: int = 20, offset: int = 0) -> list[Record]:
        filters = filters or {}
        columns = {"session_id", "task_id", "call_id", "source", "domain", "event_type",
                   "status", "lifetime"}
        if not filters.keys() <= columns or not 1 <= limit <= 100 or offset < 0:
            raise ValueError("invalid memory filters or page bounds")
        clauses = ["(expires_at IS NULL OR expires_at > ?)"]
        values: list[Any] = [time.time()]
        for key, value in filters.items():
            clauses.append(f"{key} = ?")
            values.append(value)
        # Literal substring search, including Chinese text; no semantic model dependency.
        for term in query.lower().split():
            clauses.append("instr(lower(body), ?) > 0")
            values.append(term)
        rows = self.db.execute(
            "SELECT body FROM records WHERE " + " AND ".join(clauses)
            + " ORDER BY timestamp DESC, rowid DESC LIMIT ? OFFSET ?", (*values, limit, offset),
        ).fetchall()
        return [Record.from_dict(json.loads(row[0])) for row in rows]

    def update(self, record_id: str, changes: dict[str, Any]) -> Record:
        if not changes.keys() <= {"payload", "status", "lifetime", "expires_at"}:
            raise ValueError("record identity and provenance are immutable")
        record = replace(self.get(record_id), **changes)
        if record.lifetime not in {"short", "long"}:
            raise ValueError("lifetime must be short or long")
        body = json.dumps(record.to_dict(), ensure_ascii=False, allow_nan=False)
        with self.db:
            self.db.execute("UPDATE records SET status=?, lifetime=?, expires_at=?, body=? WHERE id=?",
                            (record.status, record.lifetime, record.expires_at, body, record.id))
        return record

    def put_resource(self, data: bytes, media_type: str, description: str = "") -> ResourceRef:
        digest = sha256(data).hexdigest()
        (self.blobs / digest).write_bytes(data)
        ref = ResourceRef(uri=f"blob:{digest}", media_type=media_type, description=description)
        self.register_resource(ref)
        return ref

    def register_resource(self, ref: ResourceRef) -> None:
        with self.db:
            self.db.execute("INSERT INTO resources VALUES (?, ?)",
                            (ref.id, json.dumps(asdict(ref), ensure_ascii=False, allow_nan=False)))

    def get_resource(self, resource_id: str) -> ResourceRef:
        row = self.db.execute("SELECT body FROM resources WHERE id=?", (resource_id,)).fetchone()
        if row is None:
            raise KeyError(resource_id)
        return ResourceRef(**json.loads(row[0]))

    def read_resource(self, resource_id: str) -> bytes:
        ref = self.get_resource(resource_id)
        digest = ref.uri.removeprefix("blob:")
        if not ref.uri.startswith("blob:") or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise ValueError("external resources must be resolved by their Provider")
        return (self.blobs / digest).read_bytes()

    def cleanup(self, now: float | None = None) -> int:
        with self.db:
            cursor = self.db.execute("DELETE FROM records WHERE lifetime='short' AND expires_at<=?",
                                     (time.time() if now is None else now,))
        return cursor.rowcount

    async def maintain(self, stop: asyncio.Event, interval: float = 60.0) -> None:
        """Optional background TTL worker. It never infers facts or removes referenced blobs."""
        if interval <= 0:
            raise ValueError("maintenance interval must be positive")
        while not stop.is_set():
            self.cleanup()
            try:
                await asyncio.wait_for(stop.wait(), interval)
            except asyncio.TimeoutError:
                pass

    def save_session(self, session: dict[str, Any]) -> None:
        with self.db:
            self.db.execute("INSERT INTO sessions VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                            (session["id"], json.dumps(session, ensure_ascii=False, allow_nan=False)))

    def unfinished_calls(self) -> list[Record]:
        rows = self.db.execute("""
            SELECT r.body FROM records r WHERE r.event_type='tool_started'
            AND NOT EXISTS (SELECT 1 FROM records t WHERE t.call_id=r.call_id
                AND t.event_type IN ('tool_finished', 'verification'))
            ORDER BY r.timestamp
        """).fetchall()
        return [Record.from_dict(json.loads(row[0])) for row in rows]

    def load_session(self, session_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT body FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(session_id)
        return json.loads(row[0])

    def list_sessions(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("invalid session page bounds")
        rows = self.db.execute(
            "SELECT body FROM sessions ORDER BY json_extract(body, '$.created_at') DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def close(self) -> None:
        self.db.close()
