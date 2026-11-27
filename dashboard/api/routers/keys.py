"""Key management routes — masked only, CSRF-protected mutations."""
from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..deps import require_session, require_csrf, require_reauth, client_ip, rate_limit
from ..services import key_service, proxy_client, analytics_service
from .. import security

router = APIRouter(prefix="/api", tags=["keys"])


@router.get("/keys")
def list_keys(request: Request, limit: int = 400, offset: int = 0) -> dict:
    require_session(request)
    live = proxy_client.key_health() or {}
    keys = key_service.list_keys()
    total = len(keys)
    # The table renders a bounded page; shipping 1000+ key records per poll is
    # waste. /keys?limit=0 returns everything for export-style use.
    limit = max(0, min(int(limit), 2000))
    window = keys if limit == 0 else keys[int(offset):int(offset) + limit]
    return {
        "keys": window,
        "summary": live.get("summary", {}),
        "pool": {
            "total": total,
            "enabled": sum(1 for k in keys if k["enabled"]),
            "disabled": sum(1 for k in keys if not k["enabled"]),
        },
        "page": {"limit": limit, "offset": int(offset), "returned": len(window)},
    }


@router.get("/keys/utilisation")
def key_utilisation(request: Request) -> dict:
    require_session(request)
    return {"keys": analytics_service.key_utilisation(15)}


class ImportBody(BaseModel):
    keys: list[str]
    password: str


@router.post("/keys/import")
def import_keys(body: ImportBody, request: Request) -> dict:
    require_session(request)
    require_csrf(request)
    rate_limit(request, 10, window=60)  # max 10 imports/min
    if not require_reauth(request, body.password):
        raise HTTPException(403, "Reauthentication failed")
    result = key_service.import_keys(body.keys)
    security.audit("keys_imported", "", client_ip(request),
                   f"added={result['added']} dup={result['duplicates']}")
    return {"ok": True, **result}


class RemoveBody(BaseModel):
    fingerprint: str
    password: str


@router.post("/keys/remove")
def remove_key(body: RemoveBody, request: Request) -> dict:
    require_session(request)
    require_csrf(request)
    rate_limit(request, 20, window=60)  # max 20 removals/min
    if not require_reauth(request, body.password):
        raise HTTPException(403, "Reauthentication failed")
    ok = key_service.remove_key(body.fingerprint)
    if not ok:
        raise HTTPException(404, "Key not found")
    security.audit("key_removed", "", client_ip(request), f"fp={body.fingerprint}")
    return {"ok": True}


class ToggleBody(BaseModel):
    fingerprint: str
    enabled: bool
    password: str


@router.post("/keys/toggle")
def toggle_key(body: ToggleBody, request: Request) -> dict:
    require_session(request)
    require_csrf(request)
    rate_limit(request, 30, window=60)  # max 30 toggles/min
    if not require_reauth(request, body.password):
        raise HTTPException(403, "Reauthentication failed")
    ok = key_service.set_enabled(body.fingerprint, body.enabled)
    if not ok:
        raise HTTPException(404, "Key not found or already in that state")
    security.audit("key_enabled" if body.enabled else "key_disabled", "",
                   client_ip(request), f"fp={body.fingerprint}")
    return {"ok": True, "enabled": body.enabled,
            "note": "Keypool reloads on next proxy restart."}


class BulkBody(BaseModel):
    fingerprints: list[str]
    enabled: bool
    password: str


@router.post("/keys/bulk-toggle")
def bulk_toggle(body: BulkBody, request: Request) -> dict:
    require_session(request)
    require_csrf(request)
    rate_limit(request, 5, window=60)  # max 5 bulk ops/min
    if not require_reauth(request, body.password):
        raise HTTPException(403, "Reauthentication failed")
    changed = sum(1 for fp in body.fingerprints
                  if key_service.set_enabled(fp, body.enabled))
    security.audit("keys_bulk_toggled", "", client_ip(request),
                   f"n={changed} enabled={body.enabled}")
    return {"ok": True, "changed": changed}