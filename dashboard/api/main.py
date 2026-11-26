"""Atlas Dashboard API entrypoint — independent process from the proxy.

Run:  .venv/bin/python -m dashboard.api.main
"""
from __future__ import annotations

import sys

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse

from .config import (
    APP_HOST, APP_PORT, ALLOWED_HOSTS, WEB_DIST, PUBLIC_TLS,
)
from . import db
from .routers import auth, system, keys, config, monitor, analytics, security, models
from .services import analytics_service, log_service

app = FastAPI(title="Atlas Dashboard", version="0.2.0", docs_url=None, redoc_url=None)

# ---------------------------------------------------------------------------
# Security headers + Host allowlist
# ---------------------------------------------------------------------------
CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    host = (request.headers.get("host") or "").split(":")[0]
    if ALLOWED_HOSTS and host not in ALLOWED_HOSTS and host not in ("", "testserver"):
        return JSONResponse({"detail": "Host not allowed"}, status_code=400)
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if PUBLIC_TLS:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


# Same-origin only: no cross-origin API access.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type", "x-csrf-token"],
)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    log_service.bootstrap()
    log_service.poll()
    analytics_service.start_sampler()


@app.on_event("shutdown")
def _shutdown() -> None:
    analytics_service.stop_sampler()


app.include_router(auth.router)
app.include_router(system.router)
app.include_router(keys.router)
app.include_router(config.router)
app.include_router(monitor.router)
app.include_router(analytics.router)
app.include_router(security.router)
app.include_router(models.router)


@app.get("/api/health")
def api_health() -> dict:
    return {"status": "ok", "service": "atlas-dashboard"}


# ---------------------------------------------------------------------------
# Static frontend (served when built)
# ---------------------------------------------------------------------------
if WEB_DIST.exists() and (WEB_DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        target = WEB_DIST / full_path
        if full_path and target.is_file():
            return FileResponse(str(target))
        return FileResponse(str(WEB_DIST / "index.html"))


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=APP_HOST, port=APP_PORT, log_level="info")


if __name__ == "__main__":
    main()
