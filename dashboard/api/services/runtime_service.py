"""Runtime service — wraps the EXISTING atlas/bin/runtime.py strategy layer.

Allow-listed lifecycle actions only. No arbitrary shell.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..config import REPO_ROOT

SERVICE_NAME = "atlas-proxy.service"


def _venv_py() -> Path:
    py = REPO_ROOT / ".venv" / "bin" / "python"
    return py if py.exists() else Path(sys.executable)


def _runtime():
    atlas_dir = str(REPO_ROOT / "atlas")
    if atlas_dir not in sys.path:
        sys.path.insert(0, atlas_dir)
    from bin.runtime import get_runtime  # noqa: WPS433
    return get_runtime(REPO_ROOT, _venv_py(), SERVICE_NAME)


def status() -> dict:
    try:
        rt = _runtime()
        running, msg = rt.status()
        return {
            "running": running,
            "message": msg,
            "mode": rt.info.label,
            "session": rt.info.session,
        }
    except Exception as e:  # noqa: BLE001
        return {"running": False, "message": f"runtime error: {e}", "mode": "unknown", "session": ""}


ALLOWED_ACTIONS = {"start", "stop", "restart"}


def action(name: str) -> dict:
    if name not in ALLOWED_ACTIONS:
        return {"ok": False, "message": f"action not allowed: {name}"}
    try:
        rt = _runtime()
        if name == "start":
            ok, msg = rt.start()
        elif name == "stop":
            ok, msg = rt.stop()
        else:
            ok1, m1 = rt.stop()
            import time
            time.sleep(1)
            ok2, m2 = rt.start()
            ok, msg = ok1 and ok2, f"{m1}; {m2}"
        return {"ok": ok, "message": msg}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"runtime error: {e}"}
