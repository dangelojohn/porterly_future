"""The Parcel Ledger — an append-only, idempotent event store.

Single source of truth. Every fact about a parcel is an immutable event.
Idempotency is a UNIQUE dedupe_key: a retried command with the same key returns
the existing event instead of writing a duplicate (the fix for the legacy
double-submit / double-charge class of bugs).
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(r):
    return {
        "seq": r["seq"], "parcel_id": r["parcel_id"], "type": r["type"],
        "data": json.loads(r["data"]), "actor": r["actor"], "ts": r["ts"],
        "dedupe_key": r["dedupe_key"],
    }


class EventStore:
    def __init__(self, path=":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        # RE-ENTRANT so Core can hold it across a whole read-guard-write sequence
        # while the store methods it calls re-acquire it. Guards EVERY connection
        # access — a shared sqlite3 connection is not safe for concurrent use (even
        # read/read), and unlocked reads under ThreadingHTTPServer segfault.
        self.lock = threading.RLock()
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS events(
                 seq        INTEGER PRIMARY KEY AUTOINCREMENT,
                 parcel_id  TEXT NOT NULL,
                 type       TEXT NOT NULL,
                 data       TEXT NOT NULL,
                 actor      TEXT NOT NULL,
                 ts         TEXT NOT NULL,
                 dedupe_key TEXT UNIQUE NOT NULL)"""
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS ix_parcel ON events(parcel_id, seq)")
        self._db.commit()

    def append(self, parcel_id, type, data, actor, dedupe_key, commit=True):
        """Idempotent append. Returns (event, created:bool). Pass commit=False to STAGE
        the row in the open transaction for an atomic multi-event command; the caller then
        commit()s (or rollback()s) the whole batch so it lands all-or-nothing."""
        with self.lock:
            try:
                cur = self._db.execute(
                    "INSERT INTO events(parcel_id,type,data,actor,ts,dedupe_key) VALUES(?,?,?,?,?,?)",
                    (parcel_id, type, json.dumps(data), actor, _now(), dedupe_key),
                )
                if commit:
                    self._db.commit()
                row = self._db.execute("SELECT * FROM events WHERE seq=?", (cur.lastrowid,)).fetchone()
                return _row(row), True
            except sqlite3.IntegrityError:
                row = self._db.execute("SELECT * FROM events WHERE dedupe_key=?", (dedupe_key,)).fetchone()
                return _row(row), False

    def commit(self):
        with self.lock:
            self._db.commit()

    def rollback(self):
        with self.lock:
            self._db.rollback()

    def get_by_key(self, dedupe_key):
        with self.lock:
            row = self._db.execute("SELECT * FROM events WHERE dedupe_key=?", (dedupe_key,)).fetchone()
            return _row(row) if row else None

    def for_parcel(self, parcel_id):
        with self.lock:
            rows = self._db.execute(
                "SELECT * FROM events WHERE parcel_id=? ORDER BY seq", (parcel_id,)
            ).fetchall()
            return [_row(r) for r in rows]

    def all(self):
        with self.lock:
            return [_row(r) for r in self._db.execute("SELECT * FROM events ORDER BY seq").fetchall()]

    def count(self):
        with self.lock:
            return self._db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
