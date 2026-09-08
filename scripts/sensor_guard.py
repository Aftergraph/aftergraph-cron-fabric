"""Sensor guards (ChatGPT findings #8/#9 fix).

- classify_http: 429/5xx/timeout is SENSOR degradation, never a repo incident.
- require_evidence: ACTIONABLE+ needs a cited SHA/artifact; else refuse.
- Semaphore: max 3 concurrent GH-heavy workers (429 lesson); plus
  Retry-After respect, exponential backoff with jitter (helpers).
"""
import email.utils
import random
import sqlite3
import time
import uuid
from pathlib import Path


class SensorDegraded(Exception):
    pass


def classify_http(status=None, timeout=False):
    """Return 'REPO' or raise SensorDegraded. 429/5xx/timeout degrade."""
    if timeout or status is None:
        raise SensorDegraded("transport timeout")
    if status == 429:
        raise SensorDegraded("rate limited (429)")
    if status >= 500:
        raise SensorDegraded(f"upstream {status}")
    return "REPO"


def retry_after_seconds(header_value):
    """Parse Retry-After header (RFC 9110 §10.2.3). Returns float
    seconds or None if unparseable / non-positive. Handles both
    delta-seconds ('120') and HTTP-date ('Wed, 21 Oct 2015 07:28:00 GMT')."""
    if not header_value:
        return None
    s = header_value.strip()
    # delta-seconds
    try:
        secs = float(s)
        return secs if secs > 0 else None
    except ValueError:
        pass
    # HTTP-date
    try:
        parsed = email.utils.parsedate_tz(s)
        if parsed:
            ts = email.utils.mktime_tz(parsed)
            delta = ts - time.time()
            return delta if delta > 0 else None
    except Exception:
        pass
    return None


EVIDENCE_TYPES = {"commit", "workflow_run", "contract", "http_observation",
                  "repository_state", "cron_run", "synthetic_canary"}


def require_evidence(evidence):
    """ACTIONABLE+ requires a cited SHA/artifact. Empty -> refuse."""
    if not evidence or not str(evidence).strip():
        raise ValueError("cannot emit ACTIONABLE+: missing evidence SHA")
    return True


def require_typed_evidence(evidence):
    """Typed evidence contract (finding #3 fix): every emission carries
    type + ref + observed_at; commit findings additionally require sha."""
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be a typed dict")
    for field in ("type", "ref", "observed_at"):
        if not evidence.get(field):
            raise ValueError(f"evidence missing field: {field}")
    if evidence["type"] not in EVIDENCE_TYPES:
        raise ValueError(f"unknown evidence type: {evidence['type']}")
    if evidence["type"] == "commit" and not evidence.get("sha"):
        raise ValueError("commit evidence requires exact sha")
    return True


def backoff(attempt, base=5.0, cap=120.0):
    """Exponential backoff with jitter (seconds)."""
    return min(cap, base * (2 ** attempt)) + random.uniform(0, 1.0)


class CrossProcessSemaphore:
    """Cross-process GH budget (finding #2 fix): SQLite leases, one row
    per holder, stale leases expire via ttl. Works across independent
    cron processes on one host (BEGIN IMMEDIATE serializes writers)."""

    def __init__(self, path="state/semaphore.sqlite", limit=3, ttl=300.0):
        self.path = path
        self.limit = limit
        self.ttl = ttl
        self.lease = None
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=10.0)
        db.execute("""CREATE TABLE IF NOT EXISTS leases (
                        id TEXT PRIMARY KEY, ts REAL NOT NULL)""")
        db.commit()
        db.close()

    def _db(self):
        return sqlite3.connect(self.path, timeout=10.0,
                               isolation_level=None)

    def acquire(self):
        db = self._db()
        try:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            db.execute("DELETE FROM leases WHERE ts < ?", (now - self.ttl,))
            count = db.execute("SELECT COUNT(*) FROM leases").fetchone()[0]
            if count >= self.limit:
                db.execute("ROLLBACK")
                raise SensorDegraded("GH worker budget exhausted (3/3)")
            self.lease = uuid.uuid4().hex
            db.execute("INSERT INTO leases (id, ts) VALUES (?, ?)",
                       (self.lease, now))
            db.execute("COMMIT")
        finally:
            db.close()

    def release(self):
        if not self.lease:
            return
        db = self._db()
        try:
            db.execute("DELETE FROM leases WHERE id = ?", (self.lease,))
            db.commit()
        finally:
            db.close()
        self.lease = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False
