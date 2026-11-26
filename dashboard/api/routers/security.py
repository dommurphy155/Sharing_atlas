"""Security & account routes — password, sessions, audit."""
from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..config import SESSION_COOKIE
from ..deps import require_session, require_csrf, require_reauth, client_ip, CSRF_COOKIE
from .. import security, db

router = APIRouter(prefix="/api", tags=["security"])


@router.get("/security")
def security_overview(request: Request) -> dict:
    require_session(request)
    return {
        "sessions": security.list_sessions(),
        "audit": security.recent_audit(60),
        "current": request.cookies.get(SESSION_COOKIE, "")[:8],
    }


class PasswordBody(BaseModel):
    current: str
    new: str


@router.post("/security/password")
def change_password(body: PasswordBody, request: Request) -> dict:
    require_session(request)
    require_csrf(request)
    admin = security.get_admin()
    if not admin or not security.verify_password(body.current, admin["password_hash"]):
        security.audit("password_change_failed", "", client_ip(request))
        raise HTTPException(403, "Current password is incorrect")
    ok, msg = security.password_strength_ok(body.new)
    if not ok:
        raise HTTPException(400, msg)
    ts = db.now()
    with db.connect() as conn:
        conn.execute(
            "UPDATE admin SET password_hash = ?, password_changed_at = ? WHERE id = 1",
            (security.hash_password(body.new), ts),
        )
    sid = request.cookies.get(SESSION_COOKIE)
    n = security.revoke_all_sessions(except_sid=sid or "")
    security.audit("password_changed", admin["username"], client_ip(request),
                   f"revoked={n}")
    return {"ok": True, "revoked_sessions": n}


class RevokeBody(BaseModel):
    session_id: str = ""


@router.post("/security/sessions/revoke")
def revoke(body: RevokeBody, request: Request) -> dict:
    require_session(request)
    require_csrf(request)
    current = request.cookies.get(SESSION_COOKIE, "")
    if body.session_id:
        if body.session_id == current:
            raise HTTPException(400, "Cannot revoke the current session; log out instead")
        security.revoke_session(body.session_id)
        security.audit("session_revoked", "", client_ip(request), body.session_id[:8])
        return {"ok": True}
    n = security.revoke_all_sessions(except_sid=current)
    security.audit("sessions_revoked", "", client_ip(request), f"n={n}")
    return {"ok": True, "revoked": n}


@router.post("/auth/logout-all")
def logout_all(request: Request) -> dict:
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        security.revoke_session(sid)
    security.audit("logout", "", client_ip(request))
    return {"ok": True}