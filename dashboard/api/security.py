"""Security primitives: password hashing, setup token, sessions, audit."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Optional

from . import db
from .config import (
    SESSION_ABSOLUTE_SECONDS,
    SESSION_IDLE_SECONDS,
    SETUP_TOKEN_TTL_SECONDS,
)


# ---------------------------------------------------------------------------
# Lazy imports (so `atlas dashboard setup-token` works under system Python
# even when argon2 / pyotp aren't installed there)
# ---------------------------------------------------------------------------
def _ph():
    from argon2 import PasswordHasher
    return PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)


def _pyotp():
    import pyotp
    return pyotp


# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------
def hash_password(pw: str) -> str:
    return _ph().hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
    try:
        return _ph().verify(hashed, pw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_strength_ok(pw: str) -> tuple[bool, str]:
    """Minimum strength: >=12 chars, upper, lower, digit, symbol."""
    if len(pw) < 12:
        return False, "Password must be at least 12 characters."
    if not any(c.islower() for c in pw):
        return False, "Password must include a lowercase letter."
    if not any(c.isupper() for c in pw):
        return False, "Password must include an uppercase letter."
    if not any(c.isdigit() for c in pw):
        return False, "Password must include a number."
    if not any(not c.isalnum() for c in pw):
        return False, "Password must include a symbol."
    return True, "ok"


# ---------------------------------------------------------------------------
# Setup token (minted by CLI, single-use, hashed at rest)
# ---------------------------------------------------------------------------
def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def mint_setup_token() -> str:
    """Create (and persist) a fresh setup token. Returns the plaintext ONCE."""
    token = secrets.token_urlsafe(32)
    ts = db.now()
    with db.connect() as conn:
        conn.execute("DELETE FROM setup_token")
        conn.execute(
            "INSERT INTO setup_token (id, token_hash, created_at, expires_at, used) "
            "VALUES (1, ?, ?, ?, 0)",
            (_sha256(token), ts, ts + SETUP_TOKEN_TTL_SECONDS),
        )
    return token


def admin_exists() -> bool:
    with db.connect() as conn:
        row = conn.execute("SELECT 1 FROM admin WHERE id = 1").fetchone()
    return row is not None


def validate_setup_token(token: str) -> bool:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT token_hash, expires_at, used FROM setup_token WHERE id = 1"
        ).fetchone()
    if not row or row["used"] or row["expires_at"] < db.now():
        return False
    return hmac.compare_digest(row["token_hash"], _sha256(token))


def create_admin(username: str, password: str, totp_secret: str | None = None,
                 token: str = "", ip: str = "", ua: str = "") -> tuple[bool, str]:
    """Atomic one-time admin creation.

    No setup token required — the first password set claims the admin slot.
    totp_secret is unused (kept for signature compatibility).
    """
    ts = db.now()
    with db.connect() as conn:
        # single writer lock via IMMEDIATE-like semantics: check then write in one txn
        exists = conn.execute("SELECT 1 FROM admin WHERE id = 1").fetchone()
        if exists:
            return False, "Administrator already exists."
        conn.execute(
            "INSERT INTO admin (id, username, password_hash, totp_secret, totp_enabled, "
            "created_at, password_changed_at) VALUES (1, ?, ?, ?, 0, ?, ?)",
            (username, hash_password(password), totp_secret, ts, ts),
        )
        conn.execute(
            "INSERT INTO audit_log (ts, event, actor, ip, detail) VALUES (?,?,?,?,?)",
            (ts, "admin_created", username, ip, "initial administrator provisioned"),
        )
    return True, "ok"


def get_admin() -> Optional[dict]:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM admin WHERE id = 1").fetchone()
    return dict(row) if row else None


def verify_totp(secret: str | None, code: str | None) -> bool:
    if not secret or not code:
        return False
    try:
        return _pyotp().TOTP(secret).verify(code.strip(), valid_window=1)
    except Exception:
        return False


def new_totp_secret() -> str:
    return _pyotp().random_base32()


def totp_provisioning_uri(secret: str, username: str) -> str:
    return _pyotp().TOTP(secret).provisioning_uri(name=username, issuer_name="Atlas")


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def create_session(ip: str = "", ua: str = "") -> str:
    sid = secrets.token_urlsafe(32)
    ts = db.now()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, created_at, last_seen, expires_at, ip, user_agent, revoked) "
            "VALUES (?,?,?,?,?,?,0)",
            (sid, ts, ts, ts + SESSION_ABSOLUTE_SECONDS, ip, ua[:300]),
        )
    return sid


def get_valid_session(sid: str) -> Optional[dict]:
    if not sid:
        return None
    ts = db.now()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ? AND revoked = 0", (sid,)
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < ts or (ts - row["last_seen"]) > SESSION_IDLE_SECONDS:
            conn.execute("UPDATE sessions SET revoked = 1 WHERE id = ?", (sid,))
            return None
        conn.execute("UPDATE sessions SET last_seen = ? WHERE id = ?", (ts, sid))
    return dict(row)


def revoke_session(sid: str) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE sessions SET revoked = 1 WHERE id = ?", (sid,))


def revoke_all_sessions(except_sid: str = "") -> int:
    with db.connect() as conn:
        if except_sid:
            cur = conn.execute(
                "UPDATE sessions SET revoked = 1 WHERE id != ? AND revoked = 0", (except_sid,)
            )
        else:
            cur = conn.execute("UPDATE sessions SET revoked = 1 WHERE revoked = 0")
        return cur.rowcount


def list_sessions() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, last_seen, expires_at, ip, user_agent, revoked "
            "FROM sessions ORDER BY last_seen DESC LIMIT 50"
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
def audit(event: str, actor: str = "", ip: str = "", detail: str = "") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (ts, event, actor, ip, detail) VALUES (?,?,?,?,?)",
            (db.now(), event, actor, ip, detail),
        )


def recent_audit(limit: int = 50) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT ts, event, actor, ip, detail FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Login rate limiting
# ---------------------------------------------------------------------------
def record_login(ip: str, username: str, ok: bool) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO login_attempt (ts, ip, username, ok) VALUES (?,?,?,?)",
            (db.now(), ip, username, 1 if ok else 0),
        )


def login_locked(ip: str) -> bool:
    from .config import LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_SECONDS
    since = db.now() - LOGIN_WINDOW_SECONDS
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM login_attempt WHERE ip = ? AND ts > ? AND ok = 0",
            (ip, since),
        ).fetchone()
    return (row["n"] or 0) >= LOGIN_MAX_ATTEMPTS
