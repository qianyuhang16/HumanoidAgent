"""Explicit, read-only import of an old SQLite workspace into a new file workspace."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from primitive_vla.contracts import Record, ResourceRef
from .files import write_json
from .store import Memory


def migrate(source: str | Path, destination: str | Path) -> dict:
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError("migration destination must not exist; the source is never modified")
    uri = (source / "memory.sqlite3").as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("BEGIN")  # All tables belong to one read-only source snapshot.
        records = [Record.from_dict(json.loads(row[0])) for row in connection.execute(
            "SELECT body FROM records ORDER BY timestamp, rowid")]
        resources = [ResourceRef(**json.loads(row[0])) for row in connection.execute("SELECT body FROM resources")]
        sessions = [json.loads(row[0]) for row in connection.execute("SELECT body FROM sessions")]
    finally:
        connection.close()
    memory = Memory(destination)
    for ref in resources:
        if ref.uri.startswith("blob:"):
            digest = ref.uri.removeprefix("blob:")
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("legacy resource contains an invalid blob digest")
            data = (source / "resources" / digest).read_bytes()
            if sha256(data).hexdigest() != digest:
                raise ValueError(f"legacy blob integrity check failed for resource {ref.id}")
            (memory.blobs / digest).write_bytes(data)
        memory.register_resource(ref)
    for record in records:
        memory.write(record)
    for session in sessions:
        memory.save_session(session)
    result = {"source": str(source), "destination": str(destination), "records": len(records),
              "resources": len(resources), "sessions": len(sessions), "source_modified": False}
    write_json(destination / "migration.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("destination")
    arguments = parser.parse_args()
    print(json.dumps(migrate(arguments.source, arguments.destination), ensure_ascii=False, indent=2))
