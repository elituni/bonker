"""Root-owned SQLite state with append-only audit events."""

import json
import sqlite3

from .rules import Invalid


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS presets (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS audit_session_event ON audit
                (json_extract(value, '$.session_id'), json_extract(value, '$.event'));
            CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
                BEGIN SELECT RAISE(ABORT, 'Audit entries cannot be edited'); END;
            CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
                BEGIN SELECT RAISE(ABORT, 'Audit entries cannot be deleted'); END;
        """)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute(
            "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def append(self, record):
        self.db.execute("INSERT INTO audit(value) VALUES (?)", (json.dumps(record),))

    def audit(self):
        return [
            dict(json.loads(value), record_id=i)
            for i, value in self.db.execute("SELECT id,value FROM audit ORDER BY id DESC")
        ]

    def audit_page(self, before=None, through=None, limit=25):
        if type(limit) is not int or not 1 <= limit <= 25:
            raise Invalid("Log page size must be from 1 to 25.")
        for value in (before, through):
            if value is not None and (type(value) is not int or value < 0):
                raise Invalid("Invalid log cursor.")
        if through is None:
            through = self.db.execute("SELECT COALESCE(MAX(id),0) FROM audit").fetchone()[0]
        if before is None:
            before = through + 1
        rows = self.db.execute(
            "SELECT id,value FROM audit WHERE id<=? AND id<? ORDER BY id DESC LIMIT ?",
            (through, before, limit + 1),
        ).fetchall()
        records = []
        for record_id, value in rows[:limit]:
            record = dict(json.loads(value), record_id=record_id)
            if record.get("event") == "release_authorized":
                outcome = self.db.execute(
                    "SELECT value FROM audit WHERE id<=? AND json_extract(value,'$.session_id')=? AND json_extract(value,'$.event')='released' ORDER BY id DESC LIMIT 1",
                    (through, record["session_id"]),
                ).fetchone()
                record["released_at"] = json.loads(outcome[0])["timestamp"] if outcome else None
            records.append(record)
        return {
            "records": records,
            "through": through,
            "next_before": records[-1]["record_id"] if len(rows) > limit else None,
        }

    def presets(self):
        return [
            {"name": name, "config": json.loads(value)}
            for name, value in self.db.execute(
                "SELECT name,value FROM presets ORDER BY name COLLATE NOCASE"
            )
        ]
