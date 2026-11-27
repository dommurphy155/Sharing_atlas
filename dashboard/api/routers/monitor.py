"""Live monitoring + logs routes. Read-only, session-gated, rate-limited."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..deps import require_session, rate_limit
from ..services import log_service, proxy_client

router = APIRouter(prefix="/api", tags=["monitor"])


@router.get("/live")
def live(request: Request) -> dict:
    require_session(request)
    rate_limit(request, 60, window=60)  # 60 req/min (polling every 3s = 20/min)
    log_service.poll()
    return {
        "requests": log_service.TAIL.requests(limit=120),
        "summary": log_service.TAIL.summary(),
        "proxy": proxy_client.live_summary(),
    }


@router.get("/live/errors")
def live_errors(request: Request) -> dict:
    require_session(request)
    rate_limit(request, 60, window=60)
    log_service.poll()
    return {"errors": log_service.TAIL.recent_errors(30)}


@router.get("/logs")
def logs(request: Request, level: str = "", q: str = "", limit: int = 200) -> dict:
    require_session(request)
    rate_limit(request, 30, window=60)  # 30 req/min for log queries
    log_service.poll()
    limit = max(10, min(int(limit), 500))
    return {
        "lines": log_service.TAIL.raw_lines(limit=limit, level=level, query=q),
        "levels": ["DEBUG", "INFO", "WARNING", "ERROR", "SUCCESS"],
    }