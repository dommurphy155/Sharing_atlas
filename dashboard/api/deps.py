"""Request dependencies: session auth, CSRF, client IP."""
from __future__ import annotations

import hmac
import secrets
from typing import Optional

from fastapi import HTTPException, Request, Cookie

from . import security
from .config import SESSION_COOKIE

CSRF_COOKIE = "atlas_csrf"
CSRF_HEADER = "x-csrf-token"


def client_ip(request: Request) -> str:
    """Real client IP, proxy-aware.

    Behind nginx, request.client.host is always 127.0.0.1 — so the login
    throttle would bucket every attacker into one shared allowance and the
    audit log would record nothing useful. Trust X-Forwarded-For only because
    the app binds to loopback and nginx is the sole way in; X-Real-IP is set
    by nginx from $remote_addr and is the value we want.
    """
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        # Left-most is the original client; the rest are proxy hops.
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def csrf_token_for(request: Request) -> str:
    """Read existing CSRF cookie or mint one."""
    return request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)


def require_session(request: Request) -> dict:
    """Validate the session cookie. Called directly by route handlers."""
    sid = request.cookies.get(SESSION_COOKIE)
    sess = security.get_valid_session(sid)
    if not sess:
        raise HTTPException(status_code=401, detail="Authentication required")
    return sess


def require_csrf(request: Request) -> None:
    """Double-submit CSRF check for mutating requests."""
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def require_reauth(request: Request, password: str) -> bool:
    admin = security.get_admin()
    if not admin:
        return False
    return security.verify_password(password, admin["password_hash"])
