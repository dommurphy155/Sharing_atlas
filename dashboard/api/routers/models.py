"""Model catalogue + one-click switch routes (the `atlas switch` page)."""
from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..deps import require_session, require_csrf, require_reauth, client_ip, rate_limit
from ..services import model_service, config_service
from .. import security

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models/providers")
def providers(request: Request) -> dict:
    require_session(request)
    return {
        "providers": model_service.providers(),
        "active": config_service.read_runtime_provider(),
        "resolved": config_service.resolved(),
    }


@router.get("/models/catalogue")
def catalogue(request: Request, provider: str = "openrouter",
              refresh: bool = False, free_only: bool = True) -> dict:
    require_session(request)
    return model_service.catalogue(provider, refresh=refresh,
                                   free_only=free_only)


class SwitchBody(BaseModel):
    provider: str
    model: str
    password: str


@router.post("/models/switch")
def switch(body: SwitchBody, request: Request) -> dict:
    require_session(request)
    require_csrf(request)
    rate_limit(request, 5, window=60)  # max 5 model switches/min
    if not require_reauth(request, body.password):
        raise HTTPException(403, "Reauthentication failed")
    if not body.model.strip():
        raise HTTPException(400, "Pick a model first")
    res = model_service.switch(body.provider, body.model.strip())
    security.audit("model_switched", "", client_ip(request),
                   f"{body.provider} -> {body.model.strip()}")
    if not res.get("ok"):
        raise HTTPException(502, res.get("restart") or "restart failed")
    return res
