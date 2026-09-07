"""Event-state dedupe primitive (ChatGPT finding #1 fix).

Durable event state machine — own SQLite under state/, never Hermes'
internal state.db. One row per event_key; fingerprint decides silence.

  HEALTHY -> OPEN (new event)      emit at job severity/disposition
  OPEN -> OPEN same fingerprint    silence (already reported)
  OPEN -> OPEN new fingerprint     emit UPDATE only if material
  OPEN -> HEALTHY                  optionally emit RESOLVED
  HEALTHY -> OPEN (again)          emit again (new occurrence)

event_key   = job + subject + condition
fingerprint = sha256(event_key + relevant evidence, e.g. SHA)
"""
import hashlib
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  event_key   TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL,
  state       TEXT NOT NULL CHECK (state IN ('OPEN', 'HEALTHY')),
  opened_at   REAL NOT NULL,
  updated_at  REAL NOT NULL,
  evidence    TEXT NOT NULL DEFAULT ''
);
"""


class EventStore:
    def __init__(self, path="state/events.sqlite"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    @staticmethod
    def fingerprint(event_key, evidence):
        return hashlib.sha256(
            f"{event_key}\0{evidence}".encode("utf-8")).hexdigest()[:16]

    def check(self, event_key, evidence, material_change=True):
        """Return 'EMIT' | 'UPDATE' | 'SILENCE'. Caller emits, then record()."""
        fp = self.fingerprint(event_key, evidence)
        row = self.db.execute(
            "SELECT fingerprint, state FROM events WHERE event_key = ?",
            (event_key,)).fetchone()
        if row is None or row[1] == "HEALTHY":
            return "EMIT"
        if row[0] == fp:
            return "SILENCE"
        return "UPDATE" if material_change else "SILENCE"

    def record(self, event_key, evidence, state="OPEN"):
        fp = self.fingerprint(event_key, evidence)
        now = time.time()
        self.db.execute(
            """INSERT INTO events (event_key, fingerprint, state,
                                   opened_at, updated_at, evidence)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (event_key) DO UPDATE SET
                 fingerprint = excluded.fingerprint,
                 state = excluded.state,
                 updated_at = excluded.updated_at,
                 evidence = excluded.evidence""",
            (event_key, fp, state, now, now, evidence))
        self.db.commit()

    def resolve(self, event_key):
        self.db.execute("UPDATE events SET state = 'HEALTHY', "
                        "updated_at = ? WHERE event_key = ?",
                        (time.time(), event_key))
        self.db.commit()
