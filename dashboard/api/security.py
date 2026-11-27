"""Security primitives: password hashing, setup token, sessions, audit.

All hardening per audit: Argon2 upgrade path, TOTP encryption, password history,
session concurrency limits, exponential lockout, audit HMAC chain, recovery codes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

from . import db
from .config import (
    AUDIT_HMAC_KEY,
    DATA_DIR,
    LOCKOUT_LEVEL_COUNTS,
    LOCKOUT_THRESHOLDS,
    MAX_CONCURRENT_SESSIONS,
    PASSWORD_HISTORY,
    SESSION_ABSOLUTE_SECONDS,
    SESSION_IDLE_SECONDS,
    SETUP_TOKEN_TTL_SECONDS,
    TOTP_ENCRYPTION_KEY,
    TOTP_WINDOW,
)

# External anchor for the audit HMAC chain. Lives outside the SQLite file so
# that an attacker with write access to the DB cannot truncate the tail and
# re-forge a consistent chain without also touching this file.
AUDIT_ANCHOR_PATH = DATA_DIR / "audit_anchor.json"


# ---------------------------------------------------------------------------
# Lazy imports (so `atlas dashboard setup-token` works under system Python
# even when argon2 / pyotp aren't installed there)
# ---------------------------------------------------------------------------
def _ph():
    from argon2 import PasswordHasher
    return PasswordHasher(
        time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16,
    )


def _pyotp():
    import pyotp
    return pyotp


# ---------------------------------------------------------------------------
# TOTP secret encryption (envelope: AES-GCM if key present, else plaintext)
# ---------------------------------------------------------------------------
def _totp_encrypt(plaintext: str) -> str:
    """Encrypt TOTP secret at rest. Returns base64 or plaintext (dev)."""
    if not TOTP_ENCRYPTION_KEY:
        return plaintext  # dev mode — warn at startup
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aes = AESGCM(TOTP_ENCRYPTION_KEY[:32])
        nonce = os.urandom(12)
        ct = aes.encrypt(nonce, plaintext.encode(), None)
        return "enc:" + base64.b64encode(nonce + ct).decode()
    except ImportError:
        return plaintext  # cryptography not installed — dev fallback


def _totp_decrypt(stored: str) -> Optional[str]:
    """Decrypt TOTP secret. Returns None on failure."""
    if not stored:
        return None
    if not stored.startswith("enc:") or not TOTP_ENCRYPTION_KEY:
        return stored  # plaintext (dev or legacy)
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        raw = base64.b64decode(stored[4:])
        nonce, ct = raw[:12], raw[12:]
        aes = AESGCM(TOTP_ENCRYPTION_KEY[:32])
        return aes.decrypt(nonce, ct, None).decode()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------
def hash_password(pw: str) -> str:
    return _ph().hash(pw)


def verify_password(pw: str, hashed: str) -> tuple[bool, str | None]:
    """Verify password. Returns (ok, new_hash_if_rehash_needed).

    If the hash uses outdated parameters, returns a new hash to persist.
    """
    from argon2.exceptions import (
        VerifyMismatchError,
        VerificationError,
        InvalidHashError,
    )
    try:
        ph = _ph()
        ok = ph.verify(hashed, pw)
        if not ok:
            return False, None
        # Check if hash needs rehashing (stronger params available)
        try:
            needs_rehash = ph.check_needs_rehash(hashed)
        except Exception:
            needs_rehash = False
        return True, ph.hash(pw) if needs_rehash else None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None


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


def password_reused(pw: str) -> bool:
    """Check if password matches any of the last N in history."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT password_hash FROM password_history ORDER BY id DESC LIMIT ?",
            (PASSWORD_HISTORY,),
        ).fetchall()
    for row in rows:
        ok, _ = verify_password(pw, row["password_hash"])
        if ok:
            return True
    return False


def record_password_history(password_hash: str) -> None:
    """Store password hash in history (prune to last N)."""
    ts = db.now()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO password_history (password_hash, created_at) VALUES (?, ?)",
            (password_hash, ts),
        )
        conn.execute(
            "DELETE FROM password_history WHERE id NOT IN "
            "(SELECT id FROM password_history ORDER BY id DESC LIMIT ?)",
            (PASSWORD_HISTORY,),
        )


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

    First password set claims the admin slot. TOTP secret is encrypted at rest.
    """
    ts = db.now()
    encrypted_totp = _totp_encrypt(totp_secret) if totp_secret else None
    with db.connect() as conn:
        exists = conn.execute("SELECT 1 FROM admin WHERE id = 1").fetchone()
        if exists:
            return False, "Administrator already exists."
        conn.execute(
            "INSERT INTO admin (id, username, password_hash, totp_secret, totp_enabled, "
            "created_at, password_changed_at) VALUES (1, ?, ?, ?, ?, ?, ?)",
            (username, hash_password(password), encrypted_totp,
             1 if totp_secret else 0, ts, ts),
        )
        _queue_audit("admin_created", username, ip, "initial administrator provisioned")
    flush_pending_audit()
    return True, "ok"


def get_admin() -> Optional[dict]:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM admin WHERE id = 1").fetchone()
    if not row:
        return None
    d = dict(row)
    # Decrypt TOTP secret for callers that need it
    if d.get("totp_secret"):
        d["totp_secret"] = _totp_decrypt(d["totp_secret"])
    return d


def verify_totp(secret: str | None, code: str | None) -> bool:
    if not secret or not code:
        return False
    try:
        return _pyotp().TOTP(secret).verify(code.strip(), valid_window=TOTP_WINDOW)
    except Exception:
        return False


def new_totp_secret() -> str:
    return _pyotp().random_base32()


def totp_provisioning_uri(secret: str, username: str) -> str:
    return _pyotp().TOTP(secret).provisioning_uri(name=username, issuer_name="Atlas")


# ---------------------------------------------------------------------------
# Recovery codes (bcrypt-hashed, single-use)
# ---------------------------------------------------------------------------
def generate_recovery_codes(count: int = 10) -> list[str]:
    """Generate plaintext recovery codes (return once, store hashed)."""
    import bcrypt
    codes = []
    with db.connect() as conn:
        conn.execute("DELETE FROM recovery_code")
        for _ in range(count):
            raw = secrets.token_hex(8).upper()  # 16 hex chars
            fmt = f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:]}"
            codes.append(fmt)
            conn.execute(
                "INSERT INTO recovery_code (code_hash, used) VALUES (?, 0)",
                (bcrypt.hashpw(fmt.encode(), bcrypt.gensalt(rounds=12)).decode(),),
            )
    return codes


def verify_recovery_code(code: str) -> bool:
    """Verify and consume a recovery code. Returns True if valid.

    Standalone entry point; the throttled path uses _verify_recovery_code so
    the check and the consumption share one transaction.
    """
    with db.connect() as conn:
        return _verify_recovery_code(conn, code)


def _verify_recovery_code(conn, code: str) -> bool:
    """Verify and consume a code on an already-open connection."""
    import bcrypt
    normalized = code.strip().upper().replace(" ", "").replace("-", "")
    rows = conn.execute(
        "SELECT id, code_hash FROM recovery_code WHERE used = 0"
    ).fetchall()
    for row in rows:
        # bcrypt hash is of the formatted version
        for fmt in (code.strip().upper(), normalized):
            try:
                if bcrypt.checkpw(fmt.encode(), row["code_hash"].encode()):
                    conn.execute(
                        "UPDATE recovery_code SET used = 1 WHERE id = ?",
                        (row["id"],),
                    )
                    return True
            except Exception:
                continue
    return False


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def create_session(ip: str = "", ua: str = "") -> str:
    """Create session, enforcing concurrency limit (revoke oldest if needed)."""
    sid = secrets.token_urlsafe(32)
    ts = db.now()
    with db.connect() as conn:
        # Enforce max concurrent sessions
        active = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE revoked = 0 AND expires_at > ?",
            (ts,),
        ).fetchone()["n"]
        if active >= MAX_CONCURRENT_SESSIONS:
            to_revoke = active - MAX_CONCURRENT_SESSIONS + 1
            conn.execute(
                "UPDATE sessions SET revoked = 1 WHERE id IN "
                "(SELECT id FROM sessions WHERE revoked = 0 AND expires_at > ? "
                " ORDER BY last_seen ASC LIMIT ?)",
                (ts, to_revoke),
            )
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
# Audit (HMAC-chained for tamper evidence)
# ---------------------------------------------------------------------------
def audit_chain_configured() -> bool:
    """True when a key is present, so the chain can actually be verified."""
    return bool(AUDIT_HMAC_KEY)


def _audit_hmac(prev_hmac: str, event: str, actor: str, ip: str, detail: str, ts: int) -> str:
    if not AUDIT_HMAC_KEY:
        return ""
    msg = f"{prev_hmac}{ts}{event}{actor}{ip}{detail}".encode()
    return hmac.new(AUDIT_HMAC_KEY, msg, "sha256").hexdigest()


def _load_anchor() -> dict:
    """Read the external chain anchor.

    Returns {} only when the anchor file does not exist at all (first run).
    A file that exists but cannot be parsed is a corruption signal, not an
    absence: callers must treat it as a broken chain rather than re-baselining.
    """
    try:
        raw = AUDIT_ANCHOR_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return {"_invalid": True}
    try:
        data = json.loads(raw)
    except ValueError:
        return {"_invalid": True}
    if not isinstance(data, dict) or "last_id" not in data or "last_hmac" not in data:
        return {"_invalid": True}
    return data


def _save_anchor(last_id: int, last_hmac: str) -> None:
    """Persist the chain head outside the DB, owner-only."""
    try:
        AUDIT_ANCHOR_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = AUDIT_ANCHOR_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"last_id": last_id, "last_hmac": last_hmac}),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        os.replace(tmp, AUDIT_ANCHOR_PATH)
    except OSError:
        # An unwritable anchor degrades detection but must not stop auditing.
        pass


# Events queued by a helper that is itself inside a write transaction. The
# audit insert would otherwise deadlock on the same SQLite connection, so the
# caller flushes them once its own transaction has committed.
_PENDING_AUDIT: list[tuple[str, str, str, str]] = []


def _queue_audit(event: str, actor: str, ip: str, detail: str) -> None:
    _PENDING_AUDIT.append((event, actor, ip, detail))


def flush_pending_audit() -> None:
    """Write any events queued inside a transaction, then clear the queue."""
    pending, _PENDING_AUDIT[:] = list(_PENDING_AUDIT), []
    for event, actor, ip, detail in pending:
        audit(event, actor, ip, detail)


def anchor_audit_chain() -> None:
    """Record the current chain head externally if no anchor exists yet.

    Called at startup so rows written before the anchor file existed become the
    trusted baseline. An anchor file that exists but is unreadable/corrupt is
    never re-baselined — that would let an attacker who damaged the anchor
    silently restore truncation detection.
    """
    if AUDIT_ANCHOR_PATH.exists():
        return
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, hmac FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row:
        _save_anchor(row["id"], row["hmac"] or "")


def audit(event: str, actor: str = "", ip: str = "", detail: str = "") -> None:
    ts = db.now()
    with db.connect() as conn:
        last = conn.execute(
            "SELECT id, hmac FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev = last["hmac"] if last and last["hmac"] else ""
        chain = _audit_hmac(prev, event, actor, ip, detail, ts)
        cur = conn.execute(
            "INSERT INTO audit_log (ts, event, actor, ip, detail, hmac) VALUES (?,?,?,?,?,?)",
            (ts, event, actor, ip, detail, chain),
        )
        new_id = cur.lastrowid or 0
    _save_anchor(new_id, chain)


def verify_audit_chain() -> tuple[bool, int, str]:
    """Verify the HMAC chain.

    Returns (ok, broken_at_id, state) where state is "ok", "unconfigured"
    (no key — cannot be verified, fail closed), "anchor_invalid" (the external
    anchor exists but is unreadable, so truncation cannot be ruled out) or
    "broken".
    """
    if not AUDIT_HMAC_KEY:
        return False, 0, "unconfigured"
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, ts, event, actor, ip, detail, hmac FROM audit_log ORDER BY id"
        ).fetchall()

    prev = ""
    for row in rows:
        expected = _audit_hmac(prev, row["event"] or "", row["actor"] or "",
                               row["ip"] or "", row["detail"] or "", row["ts"])
        if not hmac.compare_digest(expected, row["hmac"] or ""):
            return False, row["id"], "broken"
        prev = row["hmac"] or ""

    # Anchor check: the DB must not have been truncated past the last head we
    # recorded externally. An invalid anchor fails closed — it is not treated
    # as "no anchor", which would silently disable truncation detection.
    anchor = _load_anchor()
    if anchor.get("_invalid"):
        return False, rows[-1]["id"] if rows else 0, "anchor_invalid"
    anchor_id = anchor.get("last_id")
    anchor_hmac = anchor.get("last_hmac")
    if anchor_id is not None and anchor_hmac is not None:
        if not rows or rows[-1]["id"] != anchor_id:
            return False, rows[-1]["id"] if rows else 0, "broken"
        if not hmac.compare_digest(rows[-1]["hmac"] or "", anchor_hmac):
            return False, rows[-1]["id"], "broken"
    return True, 0, "ok"


def recent_audit(limit: int = 50) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT ts, event, actor, ip, detail, hmac FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Login rate limiting (exponential backoff)
# ---------------------------------------------------------------------------
def record_login(ip: str, username: str, ok: bool) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO login_attempt (ts, ip, username, ok) VALUES (?,?,?,?)",
            (db.now(), ip, username, 1 if ok else 0),
        )


def _lockout_duration(fail_count: int) -> int:
    """Return lockout duration in seconds based on failure count."""
    level = 0
    for lvl, threshold in LOCKOUT_LEVEL_COUNTS.items():
        if fail_count >= threshold:
            level = lvl
    if level == 0:
        return 0
    return LOCKOUT_THRESHOLDS.get(level, LOCKOUT_THRESHOLDS[max(LOCKOUT_THRESHOLDS)])


def login_locked(ip: str) -> bool:
    """Exponential lockout: 5→15min, 10→1hr, 20→24hr."""
    return lockout_remaining(ip) > 0


def lockout_remaining(ip: str) -> int:
    """Seconds left in the current lockout window (0 = not locked).

    Also used to populate Retry-After on the 429 response.
    """
    ts = db.now()
    with db.connect() as conn:
        # Count failures in the last hour
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM login_attempt "
            "WHERE ip = ? AND ts > ? AND ok = 0",
            (ip, ts - 3600),
        ).fetchone()
        fail_count = row["n"] or 0
        if fail_count == 0:
            return 0
        lockout = _lockout_duration(fail_count)
        if lockout == 0:
            return 0
        # Check if currently locked
        last_fail = conn.execute(
            "SELECT ts FROM login_attempt WHERE ip = ? AND ok = 0 "
            "ORDER BY ts DESC LIMIT 1",
            (ip,),
        ).fetchone()
        if last_fail:
            elapsed = ts - last_fail["ts"]
            if elapsed < lockout:
                return lockout - elapsed
    return 0


# ---------------------------------------------------------------------------
# Recovery-code throttling (independent of the login lockout budget)
# ---------------------------------------------------------------------------
def recovery_lockout_remaining(ip: str) -> int:
    """Seconds left before this IP may try another recovery code."""
    ts = db.now()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM recovery_attempt "
            "WHERE ip = ? AND ts > ? AND ok = 0",
            (ip, ts - 3600),
        ).fetchone()
        fail_count = row["n"] or 0
        if fail_count == 0:
            return 0
        lockout = _lockout_duration(fail_count)
        if lockout == 0:
            return 0
        last_fail = conn.execute(
            "SELECT ts FROM recovery_attempt WHERE ip = ? AND ok = 0 "
            "ORDER BY ts DESC LIMIT 1",
            (ip,),
        ).fetchone()
        if last_fail:
            elapsed = ts - last_fail["ts"]
            if elapsed < lockout:
                return lockout - elapsed
    return 0


class RecoveryThrottled(Exception):
    """Raised when a recovery-code attempt is inside the lockout window."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("recovery attempts locked")
        self.retry_after = retry_after


def check_recovery_code(code: str, ip: str) -> bool:
    """Single entry point for recovery-code verification.

    Every caller — login fallback or the standalone verify endpoint — goes
    through here, so no path can verify a code without being throttled and
    recorded. Raises RecoveryThrottled when the IP is locked out.

    The lockout check, the code consumption and the attempt record all happen
    inside one BEGIN IMMEDIATE transaction, so concurrent requests cannot all
    observe remaining == 0 and slip past the gate, and a code cannot be
    redeemed twice.
    """
    ts = db.now()
    with db.write_lock() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM recovery_attempt "
            "WHERE ip = ? AND ts > ? AND ok = 0",
            (ip, ts - 3600),
        ).fetchone()
        fail_count = row["n"] or 0
        lockout = _lockout_duration(fail_count)
        remaining = 0
        if lockout:
            last_fail = conn.execute(
                "SELECT ts FROM recovery_attempt WHERE ip = ? AND ok = 0 "
                "ORDER BY ts DESC LIMIT 1",
                (ip,),
            ).fetchone()
            if last_fail:
                elapsed = ts - last_fail["ts"]
                if elapsed < lockout:
                    remaining = lockout - elapsed
        if remaining > 0:
            raise RecoveryThrottled(remaining)

        ok = bool(code) and _verify_recovery_code(conn, code)
        conn.execute(
            "INSERT INTO recovery_attempt (ts, ip, ok) VALUES (?,?,?)",
            (ts, ip, 1 if ok else 0),
        )
        conn.execute("DELETE FROM recovery_attempt WHERE ts < ?", (ts - 3600,))
    return ok


# ---------------------------------------------------------------------------
# Rate limiting (in-memory + DB-backed)
# ---------------------------------------------------------------------------
def rate_limit_check(key: str, limit: int, window: int = 60) -> tuple[bool, int]:
    """Check rate limit. Returns (allowed, remaining).

    Uses DB-backed counter for persistence across restarts.
    """
    ts = db.now()
    window_start = ts - (ts % window)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT count FROM rate_limit WHERE key = ? AND window_start = ?",
            (key, window_start),
        ).fetchone()
        if row:
            count = row["count"] + 1
            conn.execute(
                "UPDATE rate_limit SET count = ? WHERE key = ? AND window_start = ?",
                (count, key, window_start),
            )
        else:
            count = 1
            conn.execute(
                "INSERT OR REPLACE INTO rate_limit (key, window_start, count) VALUES (?,?,?)",
                (key, window_start, count),
            )
            # Cleanup old entries
            conn.execute("DELETE FROM rate_limit WHERE window_start < ?", (ts - 3600,))
    return count <= limit, max(0, limit - count)
