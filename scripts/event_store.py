"""Durable event-state and dedupe primitive.

Own SQLite under state/, never Hermes' internal state.db.

The authoritative emission path is claim_event(): it claims the event and
returns EMIT/UPDATE/SILENCE inside one BEGIN IMMEDIATE transaction so two
independent cron processes cannot both win the same event.

Lifecycle:
  HEALTHY -> OPEN (new observed problem)      EMIT
  OPEN -> OPEN same fingerprint               SILENCE
  OPEN -> OPEN new fingerprint                UPDATE if material
  OPEN -> HEALTHY (observed recovery only)    optionally RESOLVED upstream
  HEALTHY -> OPEN (problem observed again)    EMIT

Human acknowledgement is not resolution. acknowledge() leaves an OPEN event
OPEN, so the same unresolved fingerprint remains silent without pretending the
underlying condition recovered.
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
        # A finite timeout lets independent cron processes queue briefly while
        # BEGIN IMMEDIATE owns the single local writer slot.
        self.db = sqlite3.connect(path, timeout=30.0)
        self.db.executescript(SCHEMA)

    @staticmethod
    def fingerprint(event_key, evidence):
        return hashlib.sha256(
            f"{event_key}\0{evidence}".encode("utf-8")).hexdigest()[:16]

    def claim_event(self, event_key, evidence, material_change=True):
        """Atomically return EMIT | UPDATE | SILENCE and persist the claim.

        Callers MUST use this method before external delivery. The previous
        check() -> emit -> record() sequence was racy across independent cron
        processes because two readers could both observe an unclaimed event.
        """
        fp = self.fingerprint(event_key, evidence)
        now = time.time()
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT fingerprint, state FROM events WHERE event_key = ?",
                (event_key,)).fetchone()

            if row is None:
                self.db.execute(
                    """INSERT INTO events
                       (event_key, fingerprint, state, opened_at, updated_at, evidence)
                       VALUES (?, ?, 'OPEN', ?, ?, ?)""",
                    (event_key, fp, now, now, evidence))
                action = "EMIT"
            elif row[1] == "HEALTHY":
                self.db.execute(
                    """UPDATE events SET fingerprint = ?, state = 'OPEN',
                       opened_at = ?, updated_at = ?, evidence = ?
                       WHERE event_key = ?""",
                    (fp, now, now, evidence, event_key))
                action = "EMIT"
            elif row[0] == fp:
                action = "SILENCE"
            elif material_change:
                self.db.execute(
                    """UPDATE events SET fingerprint = ?, updated_at = ?, evidence = ?
                       WHERE event_key = ?""",
                    (fp, now, evidence, event_key))
                action = "UPDATE"
            else:
                action = "SILENCE"

            self.db.commit()
            return action
        except Exception:
            self.db.rollback()
            raise

    def check(self, event_key, evidence, material_change=True):
        """Read-only compatibility probe; do not use as the emission claim."""
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
        """Compatibility writer for fixtures/imports; claim_event is canonical."""
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

    def acknowledge(self, event_key):
        """Record human awareness without changing observed health state."""
        cur = self.db.execute(
            "UPDATE events SET updated_at = ? WHERE event_key = ? AND state = 'OPEN'",
            (time.time(), event_key))
        self.db.commit()
        return cur.rowcount == 1

    def resolve(self, event_key):
        """Mark HEALTHY only after the sensor observes the problem is gone."""
        self.db.execute("UPDATE events SET state = 'HEALTHY', "
                        "updated_at = ? WHERE event_key = ?",
                        (time.time(), event_key))
        self.db.commit()
