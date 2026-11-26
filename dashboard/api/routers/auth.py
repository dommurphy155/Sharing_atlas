"""Setup + auth routes."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Request, Response, HTTPException
from pydantic import BaseModel

from .. import security
from ..config import SESSION_COOKIE, SESSION_ABSOLUTE_SECONDS, PUBLIC_TLS
from ..deps import client_ip, require_session, require_csrf, CSRF_COOKIE

router = APIRouter(prefix="/api", tags=["auth"])

COOKIE_SECURE = PUBLIC_TLS  # Secure flag when served over TLS


# ---------------------------------------------------------------------------
# Status
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
# First-run setup — password only, no token, no username, no TOTP.
# If no admin exists, the first password set becomes the admin password.
# ---------------------------------------------------------------------------
class SetupComplete(BaseModel):
    password: str


@router.post("/setup/complete")
def setup_complete(body: SetupComplete, request: Request) -> dict:
    if security.admin_exists():
        raise HTTPException(410, "Already configured")
    ok, msg = security.password_strength_ok(body.password)
    if not ok:
        raise HTTPException(400, msg)
    created, m = security.create_admin(
        "admin", body.password, None, "", ip=client_ip(request)
    )
    if not created:
        raise HTTPException(409, m)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------
class LoginBody(BaseModel):
    password: str


@router.post("/auth/login")
def login(body: LoginBody, request: Request, response: Response) -> dict:
    ip = client_ip(request)
    if security.login_locked(ip):
        raise HTTPException(429, "Too many attempts. Try again later.")
    admin = security.get_admin()
    if not admin:
        raise HTTPException(409, "Not configured")
    ok = security.verify_password(body.password, admin["password_hash"])
    security.record_login(ip, "", ok)
    if not ok:
        security.audit("login_failed", "", ip)
        raise HTTPException(401, "Invalid credentials")

    # rotate: revoke old cookie's session if any, mint fresh
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
    sid = request.cookies.get(SESSION_COOKIE)
    admin = security.get_admin() or {}
    return {
        "username": admin.get("username", "admin"),
        "session_id": sid[:8] if sid else "",
        "session": {k: sess[k] for k in ("created_at", "expires_at", "ip") if k in sess},
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
