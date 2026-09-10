"""ActivityStore — durable SQLite store for immutable activity events.

Separate from prose logs and from the existing EventStore (which tracks
problem-state OPEN/HEALTHY events). ActivityStore persists canonical
ActivityEvent rows with deterministic event_id, payload_digest, source
cursors, and delivery/digest cursors.

Immutable: existing rows are never updated or deleted by the collector.
Duplicate observations are rejected at insert time via a UNIQUE event_id
constraint. Restart resumes from persisted cursors without replaying
already-delivered activity.

Schema version: 1
"""
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


SCHEMA_VERSION = 1

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS activity_events (
  event_id         TEXT PRIMARY KEY,
  occurred_at      TEXT NOT NULL,
  observed_at      TEXT NOT NULL,
  repo             TEXT NOT NULL,
  plane            TEXT,
  role             TEXT,
  kind             TEXT NOT NULL,
  action           TEXT NOT NULL,
  actor            TEXT NOT NULL,
  ref_type         TEXT NOT NULL,
  ref_identifier   TEXT NOT NULL,
  ref_name         TEXT,
  source_url       TEXT NOT NULL,
  source_type      TEXT NOT NULL,
  payload_digest   TEXT NOT NULL,
  display_title    TEXT,
  importance       TEXT NOT NULL DEFAULT 'normal',
  correlation_id   TEXT,
  canonical        INTEGER NOT NULL DEFAULT 1,
  created_at       TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_events_digest
  ON activity_events (repo, kind, action, ref_identifier, payload_digest);

CREATE TABLE IF NOT EXISTS source_cursors (
  source_type TEXT PRIMARY KEY,
  cursor      TEXT NOT NULL,
  etag        TEXT,
  last_success INTEGER NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delivery_cursor (
  key      TEXT PRIMARY KEY,
  cursor   TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS digest_cursor (
  key      TEXT PRIMARY KEY,
  cursor   TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repo_metadata (
  repo          TEXT PRIMARY KEY,
  archived      INTEGER NOT NULL DEFAULT 0,
  default_branch TEXT,
  observed_at   TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


class ActivityStore:
    def __init__(self, path=None):
        if path is None:
            path = os.environ.get(
                "AG_ACTIVITY_STORE",
                str(Path(__file__).resolve().parent.parent / "state" / "activity.sqlite"),
            )
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = Path(path)
        self.db = sqlite3.connect(str(self.path), timeout=30.0)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._ensure_meta()

    def _ensure_meta(self):
        cur = self.db.execute("SELECT COUNT(*) FROM meta")
        if cur.fetchone()[0] == 0:
            now = utc_now_iso()
            self.db.executescript(f"""
                INSERT INTO meta (key, value) VALUES ('schema_version', '{SCHEMA_VERSION}');
                INSERT INTO meta (key, value) VALUES ('created_at', '{now}');
            """)
            self.db.commit()

    # ---- event persistence ----

    def insert_event(self, event: dict) -> bool:
        """Persist an immutable activity event.

        Returns True on first insertion, False if a duplicate (same
        event_id or same repo+kind+action+ref_identifier+digest) already
        exists. Never updates existing rows.
        """
        now = utc_now_iso()
        row = (
            event["event_id"],
            event["occurred_at"],
            event["observed_at"],
            event["repo"],
            event.get("plane"),
            event.get("role"),
            event["kind"],
            event["action"],
            event["actor"],
            event["ref"]["type"],
            event["ref"]["identifier"],
            event["ref"].get("name"),
            event["source_url"],
            event["source_type"],
            event["payload_digest"],
            event.get("display_title"),
            event.get("importance", "normal"),
            event.get("correlation_id"),
            event.get("canonical", True),
            now,
        )
        try:
            self.db.execute(
                """INSERT INTO activity_events
                   (event_id, occurred_at, observed_at, repo, plane, role, kind,
                    action, actor, ref_type, ref_identifier, ref_name,
                    source_url, source_type, payload_digest, display_title,
                    importance, correlation_id, canonical, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                row,
            )
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            self.db.rollback()
            return False

    def event_exists(self, event_id: str) -> bool:
        cur = self.db.execute(
            "SELECT 1 FROM activity_events WHERE event_id = ?", (event_id,)
        )
        return cur.fetchone() is not None

    # ---- derived queries ----

    def recent_events(
        self,
        repo=None,
        kinds=None,
        limit=50,
        since=None,
    ):
        """Return recent canonical events, newest first.

        since: ISO-8601 string; only events observed_at >= since.
        """
        sql = "SELECT * FROM activity_events WHERE canonical = 1"
        params = []
        if repo:
            sql += " AND repo = ?"
            params.append(repo)
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            sql += f" AND kind IN ({placeholders})"
            params.extend(kinds)
        if since:
            sql += " AND observed_at >= ?"
            params.append(since)
        sql += " ORDER BY observed_at DESC LIMIT ?"
        params.append(limit)
        cur = self.db.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def stats(self, window_since=None):
        """Return aggregate stats for the current window."""
        sql = "SELECT COUNT(*) as c FROM activity_events WHERE canonical = 1"
        params = []
        if window_since:
            sql += " AND observed_at >= ?"
            params.append(window_since)
        cur = self.db.execute(sql, params)
        total = cur.fetchone()["c"]

        sql = """
            SELECT kind, action, COUNT(*) as c
            FROM activity_events
            WHERE canonical = 1
        """
        if window_since:
            sql += " AND observed_at >= ?"
            params = [window_since]
        else:
            params = []
        sql += " GROUP BY kind, action ORDER BY c DESC"
        cur = self.db.execute(sql, params)
        breakdown = [dict(r) for r in cur.fetchall()]

        return {"total": total, "breakdown": breakdown}

    # ---- source cursors ----

    def set_source_cursor(self, source_type: str, cursor: str, etag=None):
        now = utc_now_iso()
        now_ts = time.time()
        self.db.execute(
            """INSERT INTO source_cursors (source_type, cursor, etag, last_success, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(source_type) DO UPDATE SET
                 cursor = excluded.cursor,
                 etag = excluded.etag,
                 last_success = excluded.last_success,
                 updated_at = excluded.updated_at""",
            (source_type, cursor, etag, now_ts, now),
        )
        self.db.commit()

    def get_source_cursor(self, source_type: str):
        cur = self.db.execute(
            "SELECT * FROM source_cursors WHERE source_type = ?", (source_type,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "source_type": row["source_type"],
            "cursor": row["cursor"],
            "etag": row["etag"],
            "last_success": row["last_success"],
            "updated_at": row["updated_at"],
        }

    # ---- delivery / digest cursors ----

    def set_delivery_cursor(self, key: str, cursor: str):
        now = utc_now_iso()
        self.db.execute(
            """INSERT INTO delivery_cursor (key, cursor, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 cursor = excluded.cursor,
                 updated_at = excluded.updated_at""",
            (key, cursor, now),
        )
        self.db.commit()

    def get_delivery_cursor(self, key: str):
        cur = self.db.execute(
            "SELECT * FROM delivery_cursor WHERE key = ?", (key,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {"key": row["key"], "cursor": row["cursor"], "updated_at": row["updated_at"]}

    def set_digest_cursor(self, key: str, cursor: str):
        now = utc_now_iso()
        self.db.execute(
            """INSERT INTO digest_cursor (key, cursor, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 cursor = excluded.cursor,
                 updated_at = excluded.updated_at""",
            (key, cursor, now),
        )
        self.db.commit()

    def get_digest_cursor(self, key: str):
        cur = self.db.execute(
            "SELECT * FROM digest_cursor WHERE key = ?", (key,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {"key": row["key"], "cursor": row["cursor"], "updated_at": row["updated_at"]}

    # ---- repo metadata ----

    def upsert_repo_metadata(self, repo: str, archived: bool, default_branch=None):
        now = utc_now_iso()
        now_ts = time.time()
        self.db.execute(
            """INSERT INTO repo_metadata (repo, archived, default_branch, observed_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(repo) DO UPDATE SET
                 archived = excluded.archived,
                 default_branch = excluded.default_branch,
                 observed_at = excluded.observed_at,
                 updated_at = excluded.updated_at""",
            (repo, int(archived), default_branch, now, now_ts),
        )
        self.db.commit()

    def get_repo_metadata(self, repo: str):
        cur = self.db.execute(
            "SELECT * FROM repo_metadata WHERE repo = ?", (repo,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "repo": row["repo"],
            "archived": bool(row["archived"]),
            "default_branch": row["default_branch"],
            "observed_at": row["observed_at"],
            "updated_at": row["updated_at"],
        }

    def list_repo_metadata(self):
        cur = self.db.execute("SELECT * FROM repo_metadata")
        return [dict(r) for r in cur.fetchall()]

    # ---- cleanup helpers ----

    def prune_stale_repo_metadata(self, cutoff_iso: str):
        """Mark repo metadata as not observed if older than cutoff."""
        self.db.execute(
            "DELETE FROM repo_metadata WHERE observed_at < ?", (cutoff_iso,)
        )
        self.db.commit()

    def vacuum(self):
        self.db.execute("VACUUM")
        self.db.commit()
