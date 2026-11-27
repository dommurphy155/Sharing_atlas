"""SQLite persistence for dashboard-owned state only.

Tables: admin, sessions, audit_log, setup_token, login_attempt, metric_samples,
        password_history, rate_limit.
Proxy keys/config live in their existing files — never duplicated here.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator

from .config import DB_PATH, ensure_data_dir

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS admin (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    username      TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    totp_secret   TEXT,
    totp_enabled  INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL,
    password_changed_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    created_at    INTEGER NOT NULL,
    last_seen     INTEGER NOT NULL,
    expires_at    INTEGER NOT NULL,
    ip            TEXT,
    user_agent    TEXT,
    revoked       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            INTEGER NOT NULL,
    event         TEXT NOT NULL,
    actor         TEXT,
    ip            TEXT,
    detail        TEXT,
    hmac          TEXT
);

CREATE TABLE IF NOT EXISTS setup_token (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    token_hash    TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    expires_at    INTEGER NOT NULL,
    used          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS login_attempt (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            INTEGER NOT NULL,
    ip            TEXT,
    username      TEXT,
    ok            INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS metric_samples (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            INTEGER NOT NULL,
    active_reqs   INTEGER,
    total_keys    INTEGER,
    healthy_keys  INTEGER,
    avg_latency_ms REAL,
    error_rate    REAL,
    throughput    REAL
);

CREATE TABLE IF NOT EXISTS recovery_code (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code_hash     TEXT NOT NULL,
    used          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS password_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    password_hash TEXT NOT NULL,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_limit (
    key           TEXT PRIMARY KEY,
    window_start  INTEGER NOT NULL,
    count         INTEGER NOT NULL DEFAULT 0
);

-- Recovery-code attempts. Kept separate from login_attempt on purpose:
-- a failed recovery redemption must not consume the login lockout budget,
-- and a successful one must not be recorded as a failure.
CREATE TABLE IF NOT EXISTS recovery_attempt (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            INTEGER NOT NULL,
    ip            TEXT NOT NULL,
    ok            INTEGER NOT NULL DEFAULT 0
);
"""


def init_db() -> None:
    ensure_data_dir()
    with connect() as conn:
        conn.executescript(_SCHEMA)
        # Migrate: add hmac column to audit_log if missing (existing DBs)
        try:
            conn.execute("SELECT hmac FROM audit_log LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE audit_log ADD COLUMN hmac TEXT")

    # Lock down the DB file and its directory every boot: SQLite creates 0644,
    # which would expose the admin hash and session tokens to local accounts.
    harden_permissions()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def write_lock() -> Iterator[sqlite3.Connection]:
    """Serialize a read-check-write sequence against other writers.

    A plain connect() only takes its lock at the first write, so a caller that
    reads a counter, decides, then inserts can race another caller doing the
    same. BEGIN IMMEDIATE takes the write lock up front, making the whole
    sequence atomic. Readers are unaffected (WAL).
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def harden_permissions() -> None:
    """Restrict the DB, WAL, SHM, and its directory to the owner.

    The database holds the admin Argon2 hash and live session tokens. SQLite
    creates it 0644 by default, which is world-readable on a shared box — any
    local account could lift a session cookie and skip authentication
    entirely. Directories need the execute bit to be traversable, so 0700.
    WAL and SHM files inherit the DB's permissions but are created
    separately — harden them too.
    """
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(DB_PATH.parent, 0o700)
        # Harden main DB + WAL + SHM
        for suffix in ("", "-wal", "-shm"):
            p = DB_PATH.with_name(DB_PATH.name + suffix) if suffix else DB_PATH
            if p.exists():
                os.chmod(p, 0o600)
    except OSError:
        # A read-only or foreign filesystem must not stop the app booting;
        # the next successful write retries via init_db().
        pass


def now() -> int:
    return int(time.time())
