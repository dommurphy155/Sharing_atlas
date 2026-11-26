"""Configuration + provider/model routes."""
from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..deps import require_session, require_csrf, require_reauth, client_ip
from ..services import config_service
from .. import security

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
def get_config(request: Request) -> dict:
    require_session(request)
    return {
        "resolved": config_service.resolved(),
        "runtime_provider": config_service.read_runtime_provider(),
        "providers": config_service.providers(),
    }


@router.get("/config/env")
def get_env(request: Request) -> dict:
    require_session(request)
    return config_service.read_env()


class ProviderBody(BaseModel):
    provider: str
    model: str = ""
    password: str


@router.post("/config/provider")
def set_provider(body: ProviderBody, request: Request) -> dict:
    require_session(request)
    require_csrf(request)
    if not require_reauth(request, body.password):
        raise HTTPException(403, "Reauthentication failed")
    if body.provider not in config_service.providers():
        raise HTTPException(400, "Unknown provider")
    payload = config_service.write_runtime_provider(body.provider, body.model)
    security.audit("provider_changed", "", client_ip(request),
                   f"{body.provider} model={body.model}")
    return {"ok": True, "runtime_provider": payload,
            "note": "Applied on next proxy restart."}


class EnvBody(BaseModel):
    values: dict[str, str]
    password: str


@router.post("/config/env/preview")
def preview_env(body: EnvBody, request: Request) -> dict:
    require_session(request)
    require_csrf(request)
    if not require_reauth(request, body.password):
        raise HTTPException(403, "Reauthentication failed")
    return config_service.preview_env(body.values)


@router.post("/config/env")
def write_env(body: EnvBody, request: Request) -> dict:
    require_session(request)
    require_csrf(request)
    if not require_reauth(request, body.password):
        raise HTTPException(403, "Reauthentication failed")
    res = config_service.write_env(body.values)
    if not res.get("ok"):
        raise HTTPException(400, "; ".join(res.get("errors", ["Invalid values"])))
    security.audit("config_written", "", client_ip(request),
                   f"keys={len(body.values)} backup={bool(res.get('backup'))}")
    return res


class RestartBody(BaseModel):
    password: str


@router.post("/system/restart")
def restart(body: RestartBody, request: Request) -> dict:
    from ..services import runtime_service
    require_session(request)
    require_csrf(request)
    if not require_reauth(request, body.password):
        raise HTTPException(403, "Reauthentication failed")
    res = runtime_service.action("restart")
    security.audit("proxy_restarted", "", client_ip(request), res.get("message", ""))
    return res
