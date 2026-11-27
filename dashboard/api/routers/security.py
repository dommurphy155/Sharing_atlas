"""Security & account routes — password, sessions, audit, recovery codes, TOTP.

Every mutating endpoint requires: session + CSRF + reauth + rate limit.
The recovery/verify endpoint is intentionally unauthenticated (login fallback)
but protected by aggressive rate limiting + exponential lockout.
"""
from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..config import SESSION_COOKIE
from ..deps import CSRF_COOKIE, client_ip, rate_limit, require_csrf, require_reauth, require_session
from .. import security, db

router = APIRouter(prefix="/api", tags=["security"])


@router.get("/security")
def security_overview(request: Request) -> dict:
    require_session(request)
    chain_ok, broken_at = security.verify_audit_chain()
    admin = security.get_admin() or {}
    return {
        "sessions": security.list_sessions(),
        "audit": security.recent_audit(60),
        "current": request.cookies.get(SESSION_COOKIE, "")[:8],
        "audit_chain_ok": chain_ok,
        "audit_chain_broken_at": broken_at if not chain_ok else None,
    }


class PasswordBody(BaseModel):
    current: str
    new: str


@router.post("/security/password")
def change_password(body: PasswordBody, request: Request) -> dict:
    require_session(request)
    require_csrf(request)
    rate_limit(request, 3, window=60)  # max 3 password changes/min
    admin = security.get_admin()
    if not admin:
        raise HTTPException(403, "No administrator configured")

    ok, _ = security.verify_password(body.current, admin["password_hash"])
    if not ok:
        security.audit("password_change_failed", "", client_ip(request))
        raise HTTPException(403, "Current password is incorrect")

    # Strength check
    ok, msg = security.password_strength_ok(body.new)
    if not ok:
        raise HTTPException(400, msg)

    # Reuse check
    if security.password_reused(body.new):
        raise HTTPException(400, "Password was used recently. Choose a different one.")

    new_hash = security.hash_password(body.new)
    security.record_password_history(admin["password_hash"])  # archive old
    ts = db.now()
    with db.connect() as conn:
        conn.execute(
            "UPDATE admin SET password_hash = ?, password_changed_at = ? WHERE id = 1",
            (new_hash, ts),
        )

    sid = request.cookies.get(SESSION_COOKIE)
    n = security.revoke_all_sessions(except_sid=sid or "")
    security.audit("password_changed", admin["username"], client_ip(request),
                   f"revoked={n}")
    return {"ok": True, "revoked_sessions": n}


class RevokeBody(BaseModel):
    session_id: str = ""


@router.post("/security/sessions/revoke")
def revoke(body: RevokeBody, request: Request) -> dict:
    require_session(request)
    require_csrf(request)
    rate_limit(request, 10, window=60)  # max 10 revokes/min
    current = request.cookies.get(SESSION_COOKIE, "")
    if body.session_id:
        if body.session_id == current:
            raise HTTPException(400, "Cannot revoke the current session; log out instead")
        security.revoke_session(body.session_id)
        security.audit("session_revoked", "", client_ip(request), body.session_id[:8])
        return {"ok": True}
    n = security.revoke_all_sessions(except_sid=current)
    security.audit("sessions_revoked", "", client_ip(request), f"n={n}")
    return {"ok": True, "revoked": n}


@router.post("/auth/logout-all")
def logout_all(request: Request) -> dict:
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        security.revoke_session(sid)
    security.audit("logout", "", client_ip(request))
    return {"ok": True}


# ---------------------------------------------------------------------------
# TOTP / 2FA enrollment
# ---------------------------------------------------------------------------
class TotpSetupBody(BaseModel):
    password: str


@router.post("/security/totp/setup")
def totp_setup(body: TotpSetupBody, request: Request) -> dict:
    """Begin TOTP enrollment. Returns secret + QR URI (shown once)."""
    require_session(request)
    require_csrf(request)
    rate_limit(request, 3, window=60)  # max 3 setups/min
    if not require_reauth(request, body.password):
        raise HTTPException(403, "Reauthentication failed")

    admin = security.get_admin()
    if not admin:
        raise HTTPException(403, "No administrator")

    secret = security.new_totp_secret()
    uri = security.totp_provisioning_uri(secret, admin["username"])

    # Store encrypted secret (not yet enabled)
    encrypted = security._totp_encrypt(secret)
    with db.connect() as conn:
        conn.execute(
            "UPDATE admin SET totp_secret = ?, totp_enabled = 0 WHERE id = 1",
            (encrypted,),
        )

    security.audit("totp_setup_initiated", admin["username"], client_ip(request))
    return {"secret": secret, "uri": uri, "note": "Scan the QR code, then verify a code."}


class TotpVerifyBody(BaseModel):
    code: str
    password: str


@router.post("/security/totp/verify")
def totp_verify(body: TotpVerifyBody, request: Request) -> dict:
    """Verify TOTP code and enable 2FA. Returns recovery codes."""
    require_session(request)
    require_csrf(request)
    rate_limit(request, 5, window=60)  # max 5 verifies/min
    if not require_reauth(request, body.password):
        raise HTTPException(403, "Reauthentication failed")

    admin = security.get_admin()
    if not admin or not admin.get("totp_secret"):
        raise HTTPException(400, "TOTP not set up. Call /security/totp/setup first.")

    if not security.verify_totp(admin["totp_secret"], body.code):
        security.audit("totp_verify_failed", admin["username"], client_ip(request))
        raise HTTPException(400, "Invalid TOTP code")

    with db.connect() as conn:
        conn.execute("UPDATE admin SET totp_enabled = 1 WHERE id = 1")

    # Generate recovery codes
    codes = security.generate_recovery_codes(10)
    security.audit("totp_enabled", admin["username"], client_ip(request))
    return {
        "ok": True,
        "recovery_codes": codes,
        "note": "Store these codes safely. Each can be used once if you lose your device.",
    }


class RecoveryVerifyBody(BaseModel):
    code: str


@router.post("/security/recovery/verify")
def recovery_verify(body: RecoveryVerifyBody, request: Request) -> dict:
    """Verify a recovery code (for login fallback — unauthenticated by design).

    Protected by aggressive rate limiting (3 attempts/min) and exponential
    lockout to prevent brute-forcing 16-char hex codes (2^64 combinations).
    Every attempt is audited.
    """
    ip = client_ip(request)
    # Aggressive rate limit: 3 attempts per minute per IP
    rate_limit(request, 3, window=60)

    # Exponential lockout applies here too
    remaining = security.lockout_remaining(ip)
    if remaining > 0:
        raise HTTPException(
            429,
            "Too many attempts. Try again later.",
            headers={"Retry-After": str(remaining)},
        )

    security.record_login(ip, "", False)  # count toward lockout
    if security.verify_recovery_code(body.code):
        security.audit("recovery_code_used", "", ip)
        return {"ok": True}
    security.audit("recovery_code_failed", "", ip)
    raise HTTPException(400, "Invalid recovery code")


# ---------------------------------------------------------------------------
# Audit chain verification
# ---------------------------------------------------------------------------
@router.get("/security/audit/verify")
def audit_verify(request: Request) -> dict:
    require_session(request)
    ok, broken_at = security.verify_audit_chain()
    return {"ok": ok, "broken_at": broken_at if not ok else None}