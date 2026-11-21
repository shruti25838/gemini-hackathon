"""SQLite schema and access. C is the only writer of state.

One file, one instance, which suits a hackathon deployment. Every table that
records a client-initiated write carries an action_id so a repeat is replayed
rather than applied twice.

Timestamps are ISO 8601 UTC strings. Money is stored as integer minor units
plus a currency code, never as a float.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    session_id       TEXT PRIMARY KEY,
    shopper_id       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    room_revision    INTEGER NOT NULL DEFAULT 0,
    scene_revision   INTEGER NOT NULL DEFAULT 0,
    brief_revision   INTEGER NOT NULL DEFAULT 0
);

-- Every committed room. Revisions only increase.
CREATE TABLE IF NOT EXISTS room_revisions (
    session_id    TEXT NOT NULL REFERENCES sessions(session_id),
    revision      INTEGER NOT NULL,
    payload       TEXT NOT NULL,
    action_id     TEXT,
    parent        INTEGER,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (session_id, revision)
);

-- Every committed scene. Undo writes a new revision copying an older one.
CREATE TABLE IF NOT EXISTS scene_revisions (
    session_id    TEXT NOT NULL REFERENCES sessions(session_id),
    revision      INTEGER NOT NULL,
    payload       TEXT NOT NULL,
    action_id     TEXT,
    parent        INTEGER,
    undo_of       INTEGER,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (session_id, revision)
);

-- The idempotency ledger. A repeated action_id returns the stored result.
CREATE TABLE IF NOT EXISTS actions (
    action_id     TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    kind          TEXT NOT NULL,
    result        TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cart_reviews (
    review_id     TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(session_id),
    payload       TEXT NOT NULL,
    status        TEXT NOT NULL,
    payment_state TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkout_audits (
    audit_id             TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL REFERENCES sessions(session_id),
    review_id            TEXT NOT NULL,
    approval_fingerprint TEXT NOT NULL,
    cart_fingerprint     TEXT NOT NULL,
    outcome              TEXT NOT NULL,
    payload              TEXT NOT NULL,
    expires_at           TEXT NOT NULL,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      TEXT NOT NULL UNIQUE,
    session_id    TEXT NOT NULL,
    ts            TEXT NOT NULL,
    source        TEXT NOT NULL,
    type          TEXT NOT NULL,
    payload       TEXT NOT NULL,
    room_revision   INTEGER,
    scene_revision  INTEGER
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    plan_id       TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(session_id),
    payload       TEXT NOT NULL,
    state         TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- Cached catalog reads, with the ttl the source advertised.
CREATE TABLE IF NOT EXISTS offer_cache (
    cache_key     TEXT PRIMARY KEY,
    payload       TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    ttl_ms        INTEGER,
    surface       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_session ON activity_events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_cart_session ON cart_reviews(session_id);
CREATE INDEX IF NOT EXISTS idx_audit_review ON checkout_audits(review_id);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """A thin wrapper over sqlite3 with a lock, since FastAPI runs handlers
    on a thread pool and sqlite3 connections are not thread safe."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        is_memory = self.path == ":memory:"
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        if is_memory:
            # WAL is meaningless in memory and errors on some builds.
            self._conn.executescript(
                "\n".join(
                    line
                    for line in SCHEMA.splitlines()
                    if "journal_mode" not in line
                )
            )
        else:
            self._conn.executescript(SCHEMA)

    # -- plumbing ---------------------------------------------------------

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            cursor = self._conn.execute(sql, tuple(params))
            return cursor.fetchone()

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            cursor = self._conn.execute(sql, tuple(params))
            return list(cursor.fetchall())

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- sessions ---------------------------------------------------------

    def create_session(self, session_id: str, shopper_id: str) -> dict[str, Any]:
        now = utc_now_iso()
        self.execute(
            "INSERT INTO sessions (session_id, shopper_id, created_at) VALUES (?, ?, ?)",
            (session_id, shopper_id, now),
        )
        return {
            "session_id": session_id,
            "shopper_id": shopper_id,
            "created_at": now,
            "room_revision": 0,
            "scene_revision": 0,
            "brief_revision": 0,
        }

    def get_session(self, session_id: str) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM sessions WHERE session_id = ?", (session_id,))

    def current_revision(self, session_id: str, kind: str) -> int:
        column = "room_revision" if kind == "room" else "scene_revision"
        row = self.query_one(
            f"SELECT {column} AS revision FROM sessions WHERE session_id = ?", (session_id,)
        )
        return int(row["revision"]) if row else 0

    def set_current_revision(self, session_id: str, kind: str, revision: int) -> None:
        column = "room_revision" if kind == "room" else "scene_revision"
        self.execute(
            f"UPDATE sessions SET {column} = ? WHERE session_id = ?", (revision, session_id)
        )

    # -- revisions --------------------------------------------------------

    def insert_revision(
        self,
        kind: str,
        session_id: str,
        revision: int,
        payload: dict[str, Any],
        action_id: Optional[str],
        parent: Optional[int],
        undo_of: Optional[int] = None,
    ) -> None:
        table = "room_revisions" if kind == "room" else "scene_revisions"
        now = utc_now_iso()
        if kind == "room":
            self.execute(
                f"INSERT INTO {table} (session_id, revision, payload, action_id, parent, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, revision, json.dumps(payload), action_id, parent, now),
            )
        else:
            self.execute(
                f"INSERT INTO {table} (session_id, revision, payload, action_id, parent, undo_of, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, revision, json.dumps(payload), action_id, parent, undo_of, now),
            )
        self.set_current_revision(session_id, kind, revision)

    def get_revision(
        self, kind: str, session_id: str, revision: Optional[int] = None
    ) -> Optional[dict[str, Any]]:
        table = "room_revisions" if kind == "room" else "scene_revisions"
        if revision is None:
            row = self.query_one(
                f"SELECT payload FROM {table} WHERE session_id = ? ORDER BY revision DESC LIMIT 1",
                (session_id,),
            )
        else:
            row = self.query_one(
                f"SELECT payload FROM {table} WHERE session_id = ? AND revision = ?",
                (session_id, revision),
            )
        return json.loads(row["payload"]) if row else None

    # -- idempotency ------------------------------------------------------

    def get_action(self, action_id: str) -> Optional[dict[str, Any]]:
        row = self.query_one("SELECT result FROM actions WHERE action_id = ?", (action_id,))
        return json.loads(row["result"]) if row else None

    def record_action(
        self, action_id: str, session_id: str, kind: str, result: dict[str, Any]
    ) -> None:
        self.execute(
            "INSERT OR IGNORE INTO actions (action_id, session_id, kind, result, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (action_id, session_id, kind, json.dumps(result), utc_now_iso()),
        )

    # -- events -----------------------------------------------------------

    def insert_event(self, event: dict[str, Any]) -> int:
        refs = event.get("revision_refs") or {}
        cursor = self.execute(
            "INSERT INTO activity_events"
            " (event_id, session_id, ts, source, type, payload, room_revision, scene_revision)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event["event_id"],
                event["session_id"],
                event["ts"],
                event["source"],
                event["type"],
                json.dumps(event),
                refs.get("room_revision"),
                refs.get("scene_revision"),
            ),
        )
        return int(cursor.lastrowid)

    def events_since(
        self, session_id: str, since_scene_revision: Optional[int] = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        if since_scene_revision is None:
            rows = self.query_all(
                "SELECT payload FROM activity_events WHERE session_id = ?"
                " ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
        else:
            rows = self.query_all(
                "SELECT payload FROM activity_events WHERE session_id = ?"
                " AND (scene_revision IS NULL OR scene_revision > ?)"
                " ORDER BY id DESC LIMIT ?",
                (session_id, since_scene_revision, limit),
            )
        return [json.loads(row["payload"]) for row in reversed(rows)]

    # -- carts and audits -------------------------------------------------

    def upsert_cart_review(self, review: dict[str, Any]) -> None:
        now = utc_now_iso()
        self.execute(
            "INSERT INTO cart_reviews"
            " (review_id, session_id, payload, status, payment_state, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(review_id) DO UPDATE SET"
            " payload = excluded.payload, status = excluded.status,"
            " payment_state = excluded.payment_state, updated_at = excluded.updated_at",
            (
                review["review_id"],
                review["session_id"],
                json.dumps(review),
                review["status"],
                review["payment_state"],
                now,
                now,
            ),
        )

    def get_cart_review(self, review_id: str) -> Optional[dict[str, Any]]:
        row = self.query_one(
            "SELECT payload FROM cart_reviews WHERE review_id = ?", (review_id,)
        )
        return json.loads(row["payload"]) if row else None

    def latest_cart_review(self, session_id: str) -> Optional[dict[str, Any]]:
        row = self.query_one(
            "SELECT payload FROM cart_reviews WHERE session_id = ?"
            " ORDER BY updated_at DESC LIMIT 1",
            (session_id,),
        )
        return json.loads(row["payload"]) if row else None

    def insert_audit(self, audit: dict[str, Any], review_id: str) -> None:
        self.execute(
            "INSERT INTO checkout_audits"
            " (audit_id, session_id, review_id, approval_fingerprint, cart_fingerprint,"
            "  outcome, payload, expires_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                audit["audit_id"],
                audit["session_id"],
                review_id,
                audit["approval_fingerprint"],
                audit["cart_fingerprint"],
                audit["outcome"],
                json.dumps(audit),
                audit["expires_at"],
                utc_now_iso(),
            ),
        )

    def latest_audit(self, review_id: str) -> Optional[dict[str, Any]]:
        row = self.query_one(
            "SELECT payload FROM checkout_audits WHERE review_id = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (review_id,),
        )
        return json.loads(row["payload"]) if row else None

    # -- offer cache ------------------------------------------------------

    def cache_put(self, key: str, payload: Any, ttl_ms: Optional[int], surface: str) -> None:
        self.execute(
            "INSERT INTO offer_cache (cache_key, payload, fetched_at, ttl_ms, surface)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(cache_key) DO UPDATE SET payload = excluded.payload,"
            " fetched_at = excluded.fetched_at, ttl_ms = excluded.ttl_ms",
            (key, json.dumps(payload), utc_now_iso(), ttl_ms, surface),
        )

    def cache_get(self, key: str) -> Optional[tuple[Any, str, Optional[int]]]:
        row = self.query_one(
            "SELECT payload, fetched_at, ttl_ms FROM offer_cache WHERE cache_key = ?", (key,)
        )
        if row is None:
            return None
        return json.loads(row["payload"]), row["fetched_at"], row["ttl_ms"]
