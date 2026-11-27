"""Setup + auth routes."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Request, Response, HTTPException
from pydantic import BaseModel

from .. import security
from ..config import (
    PUBLIC_TLS, RATE_LIMIT_LOGIN, SESSION_ABSOLUTE_SECONDS, SESSION_COOKIE,
)
from ..deps import CSRF_COOKIE, client_ip, rate_limit, require_session

router = APIRouter(prefix="/api", tags=["auth"])

COOKIE_SECURE = PUBLIC_TLS  # Secure flag when served over TLS


# ---------------------------------------------------------------------------
# Status (public — used to determine setup vs login vs app)
# ---------------------------------------------------------------------------
@router.get("/auth/status")
def auth_status(request: Request) -> dict:
    admin = security.admin_exists()
    sess = None
    if admin:
        sid = request.cookies.get(SESSION_COOKIE)
        sess = security.get_valid_session(sid) if sid else None
    return {
        "configured": admin,
        "authenticated": bool(sess),
    }


# ---------------------------------------------------------------------------
# First-run setup — password only, no username, no TOTP.
# If no admin exists, the first password set becomes the admin password.
#
# The caller must present a setup token minted on the local console
# (`atlas dashboard setup-token`). Without it, the first person to reach the
# endpoint could claim the admin slot on a fresh install.
# ---------------------------------------------------------------------------
class SetupComplete(BaseModel):
    password: str
    token: str = ""


@router.post("/setup/complete")
def setup_complete(body: SetupComplete, request: Request) -> dict:
    # Rate limit setup attempts (prevent brute-forcing first-run)
    rate_limit(request, 3, window=60)

    if security.admin_exists():
        raise HTTPException(410, "Already configured")

    # The token is required even before an admin exists. A missing or wrong
    # token is indistinguishable from a wrong password to the caller. Consume
    # it in the same check so it cannot be replayed within its TTL.
    if not body.token or not security.validate_setup_token(body.token, consume=True):
        security.audit("setup_denied", "", client_ip(request))
        raise HTTPException(403, "A valid setup token is required")

    ok, msg = security.password_strength_ok(body.password)
    if not ok:
        raise HTTPException(400, msg)
    created, m = security.create_admin(
        "admin", body.password, None, body.token, ip=client_ip(request)
    )
    if not created:
        raise HTTPException(409, m)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------
class LoginBody(BaseModel):
    password: str
    # Required only when the admin has TOTP enabled; a recovery code is also
    # accepted here so a lost device does not lock the account out.
    code: str = ""
    recovery_code: str = ""


def _verify_second_factor(admin: dict, body: LoginBody, ip: str) -> bool:
    """Check the second factor. True when no factor is required or it passes.

    Recovery codes go through security.check_recovery_code so this path is
    throttled and recorded exactly like the standalone verify endpoint.
    Raises security.RecoveryThrottled when the IP is locked out.
    """
    if not admin.get("totp_enabled"):
        return True
    if body.code and security.verify_totp(admin.get("totp_secret"), body.code):
        return True
    if body.recovery_code and security.check_recovery_code(body.recovery_code, ip):
        return True
    return False


@router.post("/auth/login")
def login(body: LoginBody, request: Request, response: Response) -> dict:
    ip = client_ip(request)

    # Rate limiting (stricter than lockout — per-IP, per-minute)
    rate_limit(request, RATE_LIMIT_LOGIN, window=60)

    # Exponential lockout for password attempts. The recovery-code budget is
    # deliberately NOT pre-checked here: an attacker who never presents a
    # recovery code must not be able to deny password login. The budget is
    # enforced inside check_recovery_code, which only runs when a code is
    # actually presented.
    remaining = security.lockout_remaining(ip)
    if remaining > 0:
        raise HTTPException(
            429,
            "Too many attempts. Try again later.",
            headers={"Retry-After": str(remaining)},
        )

    admin = security.get_admin()
    if not admin:
        raise HTTPException(409, "Not configured")

    ok, new_hash = security.verify_password(body.password, admin["password_hash"])
    security.record_login(ip, "", ok)
    if not ok:
        security.audit("login_failed", "", ip)
        raise HTTPException(401, "Invalid credentials")

    # Second factor — only reached with a correct password, so the response
    # distinguishes "password wrong" (401) from "code required" (428).
    if admin.get("totp_enabled"):
        try:
            passed = _verify_second_factor(admin, body, ip)
        except security.RecoveryThrottled as exc:
            raise HTTPException(
                429,
                "Too many attempts. Try again later.",
                headers={"Retry-After": str(exc.retry_after)},
            )
        if not passed:
            security.audit("login_2fa_failed", admin["username"], ip)
            raise HTTPException(
                428,
                "Second factor required",
                headers={"X-2FA-Required": "totp"},
            )

    # Upgrade hash if params changed
    if new_hash:
        from .. import db
        with db.connect() as conn:
            conn.execute(
                "UPDATE admin SET password_hash = ? WHERE id = 1", (new_hash,)
            )

    # Rotate: revoke old cookie's session if any, mint fresh
    old = request.cookies.get(SESSION_COOKIE)
    if old:
        security.revoke_session(old)
    sid = security.create_session(ip, request.headers.get("user-agent", ""))
    csrf = secrets.token_urlsafe(24)
    _set_cookies(response, sid, csrf)
    security.audit("login_success", admin["username"], ip)
    return {"ok": True, "csrf_token": csrf}


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict:
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        security.revoke_session(sid)
        security.audit("logout", "", client_ip(request))
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request) -> dict:
    sess = require_session(request)
    admin = security.get_admin() or {}
    return {
        "username": admin.get("username", "admin"),
        # session_id intentionally omitted — no info leak to frontend
        "session": {
            k: sess[k] for k in ("created_at", "expires_at", "ip") if k in sess
        },
        "csrf_token": request.cookies.get(CSRF_COOKIE, ""),
    }


def _set_cookies(response: Response, sid: str, csrf: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, sid, httponly=True, secure=COOKIE_SECURE,
        samesite="strict", max_age=SESSION_ABSOLUTE_SECONDS, path="/",
    )
    # CSRF cookie is readable by JS (double-submit pattern).
    response.set_cookie(
        CSRF_COOKIE, csrf, httponly=False, secure=COOKIE_SECURE,
        samesite="strict", max_age=SESSION_ABSOLUTE_SECONDS, path="/",
    )
