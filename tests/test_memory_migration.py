from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from primitive_vla.contracts import Domain, Record, ResourceRef
from primitive_vla.memory import Memory
from primitive_vla.memory.migrate import migrate


class MemoryMigrationTests(unittest.TestCase):
    def test_read_only_migration_preserves_ids_sessions_blobs_and_unfinished_calls(self):
        with TemporaryDirectory() as directory:
            source, destination = Path(directory) / "old", Path(directory) / "new"
            (source / "resources").mkdir(parents=True)
            content = b"camera bytes"
            digest = sha256(content).hexdigest()
            (source / "resources" / digest).write_bytes(content)
            ref = ResourceRef(f"blob:{digest}", "image/png")
            record = Record("s", "t", "execution.move", Domain.EXECUTION, "tool_started", "running",
                            {"robot_id": "r", "required_resources": ["base"]}, resource_refs=(ref,), call_id="c")
            session = {"id": "s", "created_at": 1, "status": "paused"}
            database = source / "memory.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript("CREATE TABLE records(body TEXT, timestamp REAL);"
                                     "CREATE TABLE resources(body TEXT); CREATE TABLE sessions(body TEXT);")
            connection.execute("INSERT INTO records VALUES (?,?)", (json.dumps(record.to_dict()), record.timestamp))
            connection.execute("INSERT INTO resources VALUES (?)", (json.dumps(asdict(ref)),))
            connection.execute("INSERT INTO sessions VALUES (?)", (json.dumps(session),))
            connection.commit()
            connection.close()
            before = database.read_bytes()
            with self.assertRaisesRegex(ValueError, "Legacy SQLite"):
                Memory(source)
            report = migrate(source, destination)
            memory = Memory(destination)
            self.assertEqual(report["records"], 1)
            self.assertEqual(memory.get(record.id), record)
            self.assertEqual(memory.read_resource(ref.id), content)
            self.assertEqual(memory.load_session("s"), session)
            self.assertEqual(memory.unfinished_calls(), [record])
            self.assertEqual(database.read_bytes(), before)
            self.assertFalse((destination / "memory.sqlite3").exists())
            with self.assertRaises(ValueError):
                migrate(source, destination)
