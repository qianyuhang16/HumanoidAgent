"""Small SQLite task/event journal. No automatic physical action replay."""

import json
import sqlite3
from pathlib import Path
from time import time


class TaskStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, created REAL, updated REAL, status TEXT,
                instruction TEXT, config TEXT, report TEXT, state TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, payload TEXT
            );
            CREATE INDEX IF NOT EXISTS events_task ON events(task_id, seq);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
        """)
        # Keep uncertainty visible across restart; never re-dispatch saved calls.
        for row in self.db.execute("SELECT id,config FROM tasks WHERE status IN ('running','cancelling')").fetchall():
            if any(b["type"] == "http" for b in json.loads(row["config"])["backends"].values()):
                self.lock_control()
            self.db.execute("UPDATE tasks SET status='interrupted',updated=? WHERE id=?", (time(), row["id"]))
            self.event(row["id"], {"event": "interrupted", "time": time(), "reason": "Server restarted; no action replay"})
        self.db.commit()

    def create(self, task_id, instruction, config):
        with self.db:
            self.db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)",
                            (task_id, time(), time(), "running", instruction, json.dumps(config), "{}", "{}"))

    def event(self, task_id, payload):
        with self.db:
            self.db.execute("INSERT INTO events(task_id,payload) VALUES (?,?)", (task_id, json.dumps(payload)))

    def finish(self, task_id, report, state):
        with self.db:
            self.db.execute("UPDATE tasks SET status=?,report=?,state=?,updated=? WHERE id=?",
                            (report["status"], json.dumps(report), json.dumps(state), time(), task_id))

    def cancelling(self, task_id):
        with self.db:
            self.db.execute("UPDATE tasks SET status='cancelling',updated=? WHERE id=?", (time(), task_id))

    def list(self):
        return [dict(row) for row in self.db.execute("SELECT id,created,updated,status,instruction FROM tasks ORDER BY created DESC LIMIT 100")]

    def get(self, task_id):
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        record = dict(row)
        for key in ("config", "report", "state"):
            record[key] = json.loads(record[key])
        return record

    def events(self, task_id, after=0):
        return [{"seq": row["seq"], **json.loads(row["payload"])} for row in self.db.execute(
            "SELECT seq,payload FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT 500", (task_id, after))]

    def lock_control(self):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('control_locked','true')")

    @property
    def control_locked(self):
        return self.db.execute("SELECT 1 FROM metadata WHERE key='control_locked'").fetchone() is not None

    def close(self):
        self.db.close()
