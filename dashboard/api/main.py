"""Atlas Dashboard API entrypoint — independent process from the proxy.

Run:  .venv/bin/python -m dashboard.api.main
"""
from __future__ import annotations

import secrets
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import (
    ALLOWED_HOSTS, APP_HOST, APP_PORT, MAX_BODY_BYTES, PUBLIC_TLS,
    SECURITY_CONTACT, WEB_DIST,
)
from . import db
from .routers import auth, system, keys, config, monitor, analytics, security, models
from .services import analytics_service, log_service

app = FastAPI(
    title="Atlas Dashboard",
    version="0.2.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,  # Disable OpenAPI schema in production
)

# ---------------------------------------------------------------------------
# CORS — explicitly disabled. Same-origin only. No cross-origin API access.
# The CSRF middleware + Host allowlist are the enforcement layer.
# CORSMiddleware is deliberately NOT added: this is stricter than preflight.
# Any cross-origin request is blocked at the origin-check middleware.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Content-Security-Policy (hardened)
# script-src 'self' + script-src-attr 'none' blocks all XSS vectors.
# style-src 'unsafe-inline' is required for React inline layout props
# (developer-controlled, not user input — minimal risk).
# upgrade-insecure-requests forces HTTPS when available.
# ---------------------------------------------------------------------------
CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "script-src-attr 'none'; "
    "upgrade-insecure-requests;"
)

# ---------------------------------------------------------------------------
# Custom error pages (no stack traces, no info leak)
# ---------------------------------------------------------------------------
ERROR_PAGES = {
    400: ("Bad Request", "The request could not be understood."),
    401: ("Unauthorized", "Authentication is required to access this resource."),
    403: ("Forbidden", "You do not have permission to access this resource."),
    404: ("Not Found", "The requested resource could not be found."),
    405: ("Method Not Allowed", "This method is not allowed for the requested resource."),
    413: ("Payload Too Large", "The request body exceeds the allowed limit."),
    429: ("Too Many Requests", "Rate limit exceeded. Please try again later."),
    500: ("Internal Server Error", "An unexpected error occurred. Please try again."),
    502: ("Bad Gateway", "The upstream service is unavailable."),
    503: ("Service Unavailable", "The service is temporarily unavailable."),
}


def _error_response(status_code: int, detail: str = "") -> JSONResponse:
    title, default_msg = ERROR_PAGES.get(status_code, ("Error", "An error occurred."))
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": status_code,
                "title": title,
                "detail": detail or default_msg,
            }
        },
    )


# ---------------------------------------------------------------------------
# Middleware stack (order matters: outermost first)
# 1. security_headers  — Host allowlist + all security headers
# 2. csrf_protection   — cross-origin state-changing request rejection
# 3. body_size_limit   — oversized payload rejection
# 4. request_id        — correlation ID on every request/response
# ---------------------------------------------------------------------------

@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Host allowlist + security headers on every response."""
    host = (request.headers.get("host") or "").split(":")[0]
    if ALLOWED_HOSTS and host not in ALLOWED_HOSTS and host not in ("", "testserver"):
        return _error_response(400, "Host not allowed")

    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = (
        "geolocation=(), microphone=(), camera=(), "
        "payment=(), usb=(), magnetometer=(), gyroscope=(), "
        "accelerometer=(), ambient-light-sensor=(), autoplay=(), "
        "encrypted-media=(), fullscreen=(), gamepad=(), "
        "midi=(), picture-in-picture=(), publickey-credentials-get=(), "
        "screen-wake-lock=(), sync-xhr=(), web-share=(), xr-spatial-tracking=()"
    )
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    resp.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    resp.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    resp.headers["X-DNS-Prefetch-Control"] = "off"
    resp.headers["X-Download-Options"] = "noopen"
    resp.headers["X-XSS-Protection"] = "0"  # deprecated, but explicitly off
    if PUBLIC_TLS:
        resp.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains; preload"
        )
    # Hide server version
    resp.headers["Server"] = "atlas"
    return resp


@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    """Reject cross-origin state-changing requests.

    Checks Origin header against ALLOWED_HOSTS. Requests without an Origin
    header (same-origin, non-browser clients) are allowed through — the
    double-submit CSRF token check in require_csrf() covers those.
    """
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        origin = request.headers.get("origin")
        if origin:
            origin_host = urlparse(origin).netloc.split(":")[0]
            allowed = ALLOWED_HOSTS or [
                "vmi3208979.contaboserver.net", "localhost", "127.0.0.1",
            ]
            if origin_host not in allowed:
                return _error_response(403, "Cross-origin request blocked")
    return await call_next(request)


@app.middleware("http")
async def body_size_limit(request: Request, call_next):
    """Reject oversized request bodies before they're read."""
    if request.method in ("POST", "PUT", "PATCH"):
        cl = request.headers.get("content-length")
        if cl:
            try:
                if int(cl) > MAX_BODY_BYTES:
                    return _error_response(413, "Request body too large")
            except ValueError:
                return _error_response(400, "Invalid Content-Length")
    return await call_next(request)


@app.middleware("http")
async def request_id(request: Request, call_next):
    """Attach a request ID for correlation (X-Request-ID)."""
    rid = request.headers.get("x-request-id", "")
    if not rid:
        rid = secrets.token_hex(8)
    request.state.request_id = rid
    resp = await call_next(request)
    resp.headers["X-Request-ID"] = rid
    return resp


# ---------------------------------------------------------------------------
# Custom exception handlers (override FastAPI defaults — no stack traces)
# ---------------------------------------------------------------------------
@app.exception_handler(404)
async def not_found(request: Request, exc):
    return _error_response(404)


@app.exception_handler(405)
async def method_not_allowed(request: Request, exc):
    return _error_response(405)


@app.exception_handler(413)
async def payload_too_large(request: Request, exc):
    return _error_response(413)


@app.exception_handler(429)
async def rate_limited(request: Request, exc):
    # Preserve Retry-After / X-RateLimit-* set by the rate limiter or lockout
    headers = getattr(exc, "headers", None) or {}
    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "code": 429,
                "title": "Too Many Requests",
                "detail": "Rate limit exceeded. Please try again later.",
            }
        },
        headers=headers,
    )


@app.exception_handler(500)
async def internal_error(request: Request, exc):
    return _error_response(500)


# ---------------------------------------------------------------------------
# Security.txt (RFC 9116)
# ---------------------------------------------------------------------------
@app.get("/.well-known/security.txt", response_class=PlainTextResponse)
async def security_txt():
    return (
        f"Contact: mailto:{SECURITY_CONTACT}\n"
        "Expires: 2026-12-31T23:59:59.000Z\n"
        "Preferred-Languages: en\n"
        "Canonical: https://atlas.local/.well-known/security.txt\n"
        "Policy: https://atlas.local/security\n"
    )


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------
@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    log_service.bootstrap()
    log_service.poll()
    analytics_service.start_sampler()

    from . import security
    from .config import AUDIT_HMAC_KEY, PUBLIC_TLS, TOTP_ENCRYPTION_KEY

    # Anchor any audit rows that predate the external anchor, so the first
    # verification after an upgrade has something to compare against.
    security.anchor_audit_chain()

    if not TOTP_ENCRYPTION_KEY:
        import logging
        logging.warning(
            "DASHBOARD_TOTP_KEY not set — TOTP secrets stored in plaintext. "
            "Set a 32-byte key for production."
        )
    if not AUDIT_HMAC_KEY:
        import logging
        if PUBLIC_TLS:
            # Fail closed: a tamper-evident audit log is a hard requirement
            # for a publicly reachable admin surface.
            raise RuntimeError(
                "DASHBOARD_AUDIT_HMAC_KEY is not set but DASHBOARD_PUBLIC_TLS "
                "is enabled. Refusing to start without a tamper-evident audit "
                "chain; set a 32-byte key or disable public access."
            )
        logging.warning(
            "DASHBOARD_AUDIT_HMAC_KEY not set — audit chain not tamper-evident. "
            "Set a 32-byte key before exposing this instance."
        )


@app.on_event("shutdown")
def _shutdown() -> None:
    analytics_service.stop_sampler()


# ---------------------------------------------------------------------------
# Routers — every router requires session auth (except auth + security.txt)
# ---------------------------------------------------------------------------
app.include_router(auth.router)        # /auth/* — login, logout, status (public)
app.include_router(system.router)      # /overview, /system/* — session required
app.include_router(keys.router)        # /keys/* — session + CSRF + reauth
app.include_router(config.router)      # /config/* — session + CSRF + reauth
app.include_router(monitor.router)     # /live, /logs — session required
app.include_router(analytics.router)   # /analytics/* — session required
app.include_router(security.router)    # /security/* — session + CSRF + reauth
app.include_router(models.router)      # /models/* — session + CSRF + reauth


@app.get("/api/health")
def api_health() -> dict:
    """Health check — no auth required (load balancer probe)."""
    return {"status": "ok", "service": "atlas-dashboard"}


# ---------------------------------------------------------------------------
# Static frontend (served when built)
# ---------------------------------------------------------------------------
if WEB_DIST.exists() and (WEB_DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # API paths fall through to 404 handler (not SPA)
        if full_path.startswith("api/"):
            return _error_response(404)
        target = WEB_DIST / full_path
        if full_path and target.is_file():
            return FileResponse(str(target))
        return FileResponse(str(WEB_DIST / "index.html"))


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=APP_HOST, port=APP_PORT, log_level="info")


if __name__ == "__main__":
    main()
