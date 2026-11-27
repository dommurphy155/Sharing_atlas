"""Analytics routes — sampled history and log-derived breakdowns."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..deps import require_session, rate_limit
from ..services import analytics_service

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/analytics")
def analytics(request: Request) -> dict:
    require_session(request)
    rate_limit(request, 30, window=60)
    return analytics_service.overview()


@router.get("/analytics/series")
def series(request: Request, hours: int = 6) -> dict:
    require_session(request)
    rate_limit(request, 30, window=60)
    hours = max(1, min(int(hours), 72))
    return {"series": analytics_service.series(hours=hours)}


@router.get("/analytics/models")
def models(request: Request) -> dict:
    require_session(request)
    rate_limit(request, 30, window=60)
    return {"models": analytics_service.model_usage()}


@router.get("/analytics/keys")
def keys(request: Request) -> dict:
    require_session(request)
    rate_limit(request, 30, window=60)
    return {"keys": analytics_service.key_utilisation()}
