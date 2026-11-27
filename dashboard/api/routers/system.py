"""Overview + system management routes (live proxy state)."""
from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..deps import require_session, require_csrf, require_reauth, client_ip, rate_limit
from ..services import (
    proxy_client, runtime_service, config_service, key_service,
    system_service, analytics_service, log_service,
)
from .. import security

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/overview")
def overview(request: Request) -> dict:
    require_session(request)
    log_service.poll()
    live = proxy_client.live_summary()
    cfg = config_service.resolved()
    rt = runtime_service.status()
    rp = config_service.read_runtime_provider()

    detail = proxy_client.key_health().get("keys", []) or []
    latencies = [d.get("last_latency_ms") for d in detail if d.get("last_latency_ms")]
    avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else None

    total_req = sum(d.get("total_requests", 0) for d in detail)
    total_err = sum(d.get("total_errors", 0) for d in detail)
    error_rate = round((total_err / total_req) * 100, 2) if total_req else 0.0
    tail = log_service.TAIL.summary()

    return {
        "live": live,
        "runtime": rt,
        "config": cfg,
        "runtime_provider": rp,
        "metrics": {
            "active_requests": live["keys"]["in_flight"],
            "total_requests": total_req,
            "avg_latency_ms": avg_latency,
            "error_rate_pct": error_rate,
            "session_requests": tail["total"],
            "session_failed": tail["failed"],
        },
        "series": analytics_service.series(hours=3, bucket=300),
        "models": analytics_service.model_usage(6),
        "errors": log_service.TAIL.recent_errors(5),
        "audit": security.recent_audit(8),
        "has_latency_history": bool(latencies),
    }


@router.get("/system/status")
def system_status(request: Request) -> dict:
    require_session(request)
    unit = system_service.proxy_unit_status()
    return {
        "runtime": runtime_service.status(),
        "proxy": proxy_client.health() or {"status": "unreachable"},
        "proxy_unit": unit,
        "dashboard_unit": system_service.unit_status(system_service.DASHBOARD_SERVICE),
        "process": system_service.process_info(unit.get("pid", 0)),
        "version": system_service.version_info(),
        "health": system_service.health_checks(),
        "provider": config_service.read_runtime_provider(),
    }


@router.get("/system/diagnostics")
def diagnostics(request: Request) -> dict:
    require_session(request)
    return system_service.diagnostics()


@router.get("/system/updates")
def updates(request: Request) -> dict:
    require_session(request)
    return system_service.check_updates()


@router.get("/system/backups")
def backups(request: Request) -> dict:
    require_session(request)
    return {"backups": system_service.list_backups()}


class ActionBody(BaseModel):
    password: str


@router.post("/system/{unit}/{action}")
def unit_action(unit: str, action: str, body: ActionBody, request: Request) -> dict:
    require_session(request)
    require_csrf(request)
    rate_limit(request, 5, window=60)  # max 5 system actions/min
    if not require_reauth(request, body.password):
        raise HTTPException(403, "Reauthentication failed")
    if unit not in ("proxy", "dashboard"):
        raise HTTPException(400, "Unknown unit")
    if action not in system_service.ALLOWED:
        raise HTTPException(400, "Action not allowed")
    res = (system_service.proxy_action(action) if unit == "proxy"
           else system_service.dashboard_action(action))
    security.audit(f"{unit}_{action}", "", client_ip(request), res.get("message", ""))
    if not res.get("ok"):
        raise HTTPException(502, res.get("message", "action failed"))
    return res


class BackupBody(BaseModel):
    password: str
    label: str = ""


@router.post("/system/backup")
def make_backup(body: BackupBody, request: Request) -> dict:
    require_session(request)
    require_csrf(request)
    rate_limit(request, 3, window=60)  # max 3 backups/min
    if not require_reauth(request, body.password):
        raise HTTPException(403, "Reauthentication failed")
    res = system_service.backup_config(body.label.strip()[:32])
    security.audit("config_backed_up", "", client_ip(request), res.get("backup", ""))
    return res