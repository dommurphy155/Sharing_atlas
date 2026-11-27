"""Request dependencies: session auth, CSRF, client IP, rate limiting."""
from __future__ import annotations

import hmac
import ipaddress
import secrets
from typing import Optional

from fastapi import HTTPException, Request

from . import security
from .config import SESSION_COOKIE, TRUSTED_PROXIES

CSRF_COOKIE = "atlas_csrf"
CSRF_HEADER = "x-csrf-token"


def _is_trusted_proxy(ip_str: str) -> bool:
    """Check if an IP is in the trusted proxy CIDR list."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for cidr in TRUSTED_PROXIES:
        try:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request) -> str:
    """Real client IP, validated against trusted proxy chain.

    X-Real-IP is set by nginx from $remote_addr — trusted because the app
    binds to loopback and nginx is the sole ingress. X-Forwarded-For is
    only trusted if the connecting peer is a known proxy; the left-most
    non-proxy IP in the chain is the real client.
    """
    # X-Real-IP: set by nginx, trustworthy
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real

    # X-Forwarded-For: validate the chain
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        chain = [h.strip() for h in forwarded.split(",") if h.strip()]
        # Walk right-to-left; first non-trusted IP is the real client
        for hop in reversed(chain):
            if not _is_trusted_proxy(hop):
                return hop
        # All hops trusted — return left-most (edge case)
        return chain[0] if chain else "unknown"

    # Direct connection
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
    """Double-submit CSRF check for mutating requests.

    Both values must be non-empty and compare equal. The comparison is on
    ``str``; a non-ASCII header would raise inside compare_digest, so encode
    explicitly and treat any failure as a rejection rather than a 500.
    """
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not header:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    try:
        ok = hmac.compare_digest(cookie.encode("utf-8"), header.encode("utf-8"))
    except (UnicodeEncodeError, AttributeError):
        ok = False
    if not ok:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def require_reauth(request: Request, password: str) -> bool:
    """Re-authenticate with password for sensitive operations.

    Also upgrades the password hash if Argon2 params have changed.
    """
    admin = security.get_admin()
    if not admin:
        return False
    ok, new_hash = security.verify_password(password, admin["password_hash"])
    if not ok:
        return False
    # Upgrade hash if needed
    if new_hash:
        from . import db
        with db.connect() as conn:
            conn.execute(
                "UPDATE admin SET password_hash = ? WHERE id = 1", (new_hash,)
            )
    return True


def rate_limit(request: Request, limit: int, window: int = 60) -> None:
    """Enforce per-IP rate limit. Raises 429 if exceeded."""
    ip = client_ip(request)
    allowed, remaining = security.rate_limit_check(f"rl:{ip}", limit, window)
    request.state.rate_remaining = remaining
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Try again later.",
            headers={
                "Retry-After": str(window),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )
