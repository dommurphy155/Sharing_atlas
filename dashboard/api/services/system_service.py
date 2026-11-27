"""System service — lifecycle, systemd, updates, diagnostics.

Only allow-listed actions exist. No user string ever reaches a shell.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

from ..config import ATLAS_ROOT, BACKUP_DIR, ENV_FILE, VERSION

PROXY_SERVICE = "atlas-proxy.service"
DASHBOARD_SERVICE = "atlas-dashboard.service"
ALLOWED = {"start", "stop", "restart"}


def _run(cmd: List[str], timeout: float = 20.0) -> tuple[int, str]:
    """Run a fixed argv (never a shell string)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, "command not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def _systemctl(unit: str, action: str) -> Dict[str, Any]:
    if action not in ALLOWED:
        return {"ok": False, "message": f"action not allowed: {action}"}
    rc, out = _run(["systemctl", action, unit])
    return {"ok": rc == 0, "message": out or f"{action} {unit}", "code": rc}


def proxy_action(action: str) -> Dict[str, Any]:
    """Drive the proxy through the EXISTING runtime layer.

    The installation may run under systemd, tmux, or a bare process — the
    runtime layer (atlas/bin/runtime.py) already knows which and owns the
    correct start/stop/restart for it. Calling systemctl here would break any
    install that is not systemd-managed.
    """
    from . import runtime_service
    return runtime_service.action(action)


def proxy_unit_status() -> Dict[str, Any]:
    """Status of the proxy under whatever supervisor actually owns it."""
    from . import runtime_service
    rt = runtime_service.status()
    unit = unit_status(PROXY_SERVICE)
    mode = (rt.get("mode") or "unknown").replace(" (tmux)", "").strip()
    # The systemd unit is authoritative only when it is the live supervisor.
    if unit.get("active") == "active":
        return {**unit, "supervisor": "systemd", "mode": rt.get("mode", "")}
    return {
        "unit": mode,
        "active": "active" if rt.get("running") else "inactive",
        "sub": rt.get("session", ""),
        "pid": 0,
        "since": rt.get("message", ""),
        "loaded": True,
        "supervisor": mode,
        "mode": rt.get("mode", ""),
    }


def dashboard_action(action: str) -> Dict[str, Any]:
    return _systemctl(DASHBOARD_SERVICE, action)


def unit_status(unit: str) -> Dict[str, Any]:
    rc, out = _run(["systemctl", "show", unit,
                    "--property=ActiveState,SubState,MainPID,ExecMainStartTimestamp",
                    "--no-pager"])
    fields = dict(
        line.split("=", 1) for line in out.splitlines() if "=" in line
    )
    return {
        "unit": unit,
        "active": fields.get("ActiveState", "unknown") if rc == 0 else "unknown",
        "sub": fields.get("SubState", ""),
        "pid": int(fields.get("MainPID") or 0),
        "since": fields.get("ExecMainStartTimestamp", ""),
        "loaded": rc == 0,
    }


def process_info(pid: int) -> Dict[str, Any]:
    if not pid:
        return {}
    try:
        stat = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return {}
    info: Dict[str, Any] = {}
    for line in stat.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    try:
        info["cmdline"] = Path(f"/proc/{pid}/cmdline").read_text().replace("\0", " ").strip()
    except OSError:
        pass
    return {
        "pid": pid,
        "state": info.get("State", ""),
        "vms": info.get("VmRSS", ""),
        "threads": info.get("Threads", ""),
        "cmdline": info.get("cmdline", ""),
    }


def health_checks() -> Dict[str, Any]:
    from . import proxy_client
    checks = []
    h = proxy_client.health()
    checks.append({"name": "proxy reachable", "ok": h is not None,
                   "detail": (h or {}).get("status", "unreachable")})
    kh = proxy_client.key_health() or {}
    summary = kh.get("summary", {})
    checks.append({
        "name": "keypool has healthy keys",
        "ok": (summary.get("healthy", 0) > 0),
        "detail": f"{summary.get('healthy', 0)}/{summary.get('total', 0)} healthy",
    })
    checks.append({"name": "dashboard database", "ok": _db_ok(),
                   "detail": "write/read ok" if _db_ok() else "unavailable"})
    checks.append({"name": "file permissions", "ok": _perms_ok(),
                   "detail": _perms_detail()})
    return {"checks": checks, "ok": all(c["ok"] for c in checks)}


def _perms_ok() -> bool:
    """Verify critical files have owner-only permissions."""
    import stat
    from ..config import DB_PATH, ENV_FILE
    critical = [DB_PATH, ENV_FILE, _key_file()]
    for p in critical:
        try:
            st = p.stat()
            mode = stat.S_IMODE(st.st_mode)
            if mode & 0o077:  # group or other has any access
                return False
        except OSError:
            continue
    return True


def _perms_detail() -> str:
    import stat
    from ..config import DB_PATH, ENV_FILE
    issues = []
    for p in [DB_PATH, ENV_FILE, _key_file()]:
        try:
            mode = stat.S_IMODE(p.stat().st_mode)
            if mode & 0o077:
                issues.append(f"{p.name}={oct(mode)}")
        except OSError:
            pass
    return ", ".join(issues) if issues else "all 0600"


def _db_ok() -> bool:
    try:
        from .. import db
        with db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except Exception:  # noqa: BLE001
        return False


def version_info() -> Dict[str, Any]:
    rc, out = _run(["git", "-C", str(ATLAS_ROOT), "log", "-1", "--format=%h %s"])
    branch = _run(["git", "-C", str(ATLAS_ROOT), "rev-parse", "--abbrev-ref", "HEAD"])[1]
    return {
        "dashboard": VERSION,
        "commit": out if rc == 0 else "unknown",
        "branch": branch,
        "dirty": bool(_run(["git", "-C", str(ATLAS_ROOT), "status", "--porcelain"])[1]),
    }


def check_updates() -> Dict[str, Any]:
    rc, out = _run(["git", "-C", str(ATLAS_ROOT), "fetch", "--dry-run"], timeout=30)
    behind = _run(["git", "-C", str(ATLAS_ROOT), "rev-list", "--count", "HEAD..@{u}"])
    n = int(behind[1].strip() or 0) if behind[0] == 0 else 0
    return {"available": n > 0, "behind": n, "detail": out if rc else "up to date"}


def backup_config(label: str = "") -> Dict[str, Any]:
    """Copy the credential/config surface into an owner-only backup dir."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = f"{stamp}{'_' + label if label else ''}"
    dest = BACKUP_DIR / name
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    from ..config import DATA_DIR, PROXY_LOG_DIR
    for src in (ENV_FILE, ATLAS_ROOT / "data" / "proxy_data"):
        if src.exists():
            tgt = dest / src.name
            if src.is_dir():
                shutil.copytree(src, tgt, dirs_exist_ok=True)
            else:
                shutil.copy2(src, tgt)
                os.chmod(tgt, 0o600)
            copied.append(str(src))
    keyfile = _key_file()
    if keyfile.exists():
        tgt = dest / keyfile.name
        shutil.copy2(keyfile, tgt)
        os.chmod(tgt, 0o600)
        copied.append(str(keyfile))
    return {"ok": True, "backup": str(dest), "copied": copied}


def _key_file() -> Path:
    from .config_service import _proxy_config
    return Path(_proxy_config().KEY_FILE)


def list_backups() -> List[Dict[str, Any]]:
    if not BACKUP_DIR.exists():
        return []
    out = []
    for d in sorted(BACKUP_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if d.is_dir():
            out.append({"name": d.name, "ts": int(d.stat().st_mtime),
                        "path": str(d)})
    return out[:20]


def diagnostics() -> Dict[str, Any]:
    from . import proxy_client
    return {
        "version": version_info(),
        "proxy_unit": proxy_unit_status(),
        "dashboard_unit": unit_status(DASHBOARD_SERVICE),
        "proxy": proxy_client.health(),
        "health": health_checks(),
        "env_file": str(ENV_FILE),
        "env_present": ENV_FILE.exists(),
        "log": str(_log_path()),
        "disk_free_gb": round(shutil.disk_usage(str(ATLAS_ROOT)).free / 1e9, 2),
    }


def _log_path() -> Path:
    from ..config import PROXY_LOG
    return PROXY_LOG
