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
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(tasks)")}
        for name in ("conversation_id", "parent_id"):
            if name not in columns:
                self.db.execute(f"ALTER TABLE tasks ADD COLUMN {name} TEXT")
        self.db.execute("UPDATE tasks SET conversation_id=id WHERE conversation_id IS NULL")
        self.db.execute("CREATE INDEX IF NOT EXISTS tasks_conversation ON tasks(conversation_id,created)")
        # Keep uncertainty visible across restart; never re-dispatch saved calls.
        for row in self.db.execute("SELECT id,config FROM tasks WHERE status IN ('running','cancelling')").fetchall():
            self.db.execute("UPDATE tasks SET status='interrupted',updated=? WHERE id=?", (time(), row["id"]))
            self.event(row["id"], {"event": "interrupted", "time": time(), "reason": "Server restarted; no action replay"})
        self.db.commit()
        if self.db.execute("SELECT 1 FROM metadata WHERE key LIKE 'active_control:%'").fetchone():
            self.lock_control()

    def create(self, task_id, instruction, config, *, conversation_id=None, parent_id=None):
        with self.db:
            self.db.execute("INSERT INTO tasks (id,created,updated,status,instruction,config,report,state,conversation_id,parent_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (task_id, time(), time(), "running", instruction, json.dumps(config), "{}", "{}",
                             conversation_id or task_id, parent_id))

    def event(self, task_id, payload):
        with self.db:
            self.db.execute("INSERT INTO events(task_id,payload) VALUES (?,?)", (task_id, json.dumps(payload)))
            if payload.get("event") == "tool_started" and not payload.get("read_only", True):
                self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)",
                                ("active_control:" + payload["call"]["id"], task_id))
            if payload.get("event") == "tool_finished":
                if payload["result"].get("control_released", True):
                    self.db.execute("DELETE FROM metadata WHERE key=?", ("active_control:" + payload["call"]["id"],))
                else:
                    self.lock_control()

    def finish(self, task_id, report, state):
        with self.db:
            self.db.execute("UPDATE tasks SET status=?,report=?,state=?,updated=? WHERE id=?",
                            (report["status"], json.dumps(report), json.dumps(state), time(), task_id))

    def cancelling(self, task_id):
        with self.db:
            self.db.execute("UPDATE tasks SET status='cancelling',updated=? WHERE id=?", (time(), task_id))

    def list(self):
        return [dict(row) for row in self.db.execute("SELECT id,created,updated,status,instruction,conversation_id,parent_id FROM tasks ORDER BY created DESC LIMIT 100")]

    def conversation(self, conversation_id):
        return [dict(row) for row in self.db.execute(
            "SELECT id,created,status,instruction,report FROM tasks WHERE conversation_id=? ORDER BY created,rowid",
            (conversation_id,))]

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
