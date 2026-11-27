"""Dashboard setup: create the admin, then start the dashboard.

Cross-platform by construction — no systemd, no shell, no ``&``. The dashboard
is launched as a detached child of this process (Windows: new process group;
POSIX: new session) and then polled over loopback HTTP until it answers.

Passwords are read with ``getpass`` so they are never echoed and never land in
shell history. They are handed to the setup child on stdin, never as an argv
element: argv is world-readable via ``ps`` and ``/proc/<pid>/cmdline``, while
stdin is not.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DASHBOARD_LOG = "data/dashboard.log"
DASHBOARD_PID = "data/dashboard.pid"

# Loopback must never go through a system or VPN proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------
def dashboard_pid_file(repo: Path) -> Path:
    return repo / DASHBOARD_PID


def dashboard_log_file(repo: Path) -> Path:
    return repo / DASHBOARD_LOG


def dashboard_config(repo: Path, venv_py: Path) -> dict:
    """Read the dashboard's effective host/port without importing the app."""
    sys.path.insert(0, str(repo))
    try:
        from dashboard.api.config import APP_HOST, APP_PORT  # type: ignore
        return {"host": APP_HOST, "port": int(APP_PORT)}
    except Exception:
        return {"host": "127.0.0.1", "port": 8080}
    finally:
        try:
            sys.path.remove(str(repo))
        except ValueError:
            pass


def _base_url(cfg: dict) -> str:
    host = cfg["host"]
    # A wildcard bind is not a connectable address.
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return f"http://{host}:{cfg['port']}"


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
def health_check(repo: Path, timeout: float = 20.0) -> tuple[bool, str]:
    """Bounded poll of the dashboard /api/health endpoint."""
    cfg = dashboard_config(repo, Path(sys.executable))
    url = f"{_base_url(cfg)}/api/health"
    deadline = time.monotonic() + timeout
    last = "no response"
    while time.monotonic() < deadline:
        try:
            with _OPENER.open(url, timeout=1.5) as r:
                if 200 <= r.status < 300:
                    return True, f"healthy ({r.status}) @ {url}"
                last = f"status {r.status}"
        except urllib.error.HTTPError as e:
            last = f"http {e.code}"
        except (urllib.error.URLError, ConnectionError, OSError):
            last = "unreachable"
        time.sleep(0.4)
    return False, f"unhealthy: {last} ({url})"


def port_in_use(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Admin creation
# ---------------------------------------------------------------------------
def _create_admin(repo: Path, venv_py: Path, password: str) -> tuple[bool, str]:
    """Create the admin row. Returns (ok, message).

    Runs in a subprocess so a failure can never take the installer down, and so
    the dashboard package is imported against the venv's dependencies.
    """
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(repo)!r})\n"
        "from dashboard.api import db, security\n"
        "db.init_db()\n"
        "pw = sys.stdin.readline().rstrip('\\n')\n"
        "ok, msg = security.create_admin('admin', pw)\n"
        "print('OK' if ok else 'ERR:' + msg)\n"
    )
    try:
        p = subprocess.run(
            [str(venv_py), "-c", code],
            input=password + "\n",
            capture_output=True, text=True, timeout=120, cwd=str(repo),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"could not run setup: {e}"
    out = (p.stdout or "").strip().splitlines()
    last = out[-1] if out else ""
    if p.returncode == 0 and last == "OK":
        return True, "admin created"
    if last.startswith("ERR:"):
        return False, last[4:]
    return False, (p.stderr or p.stdout or "unknown error").strip()[-300:]


def admin_exists(repo: Path, venv_py: Path) -> bool:
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(repo)!r})\n"
        "from dashboard.api import db, security\n"
        "db.init_db()\n"
        "print('YES' if security.admin_exists() else 'NO')\n"
    )
    try:
        p = subprocess.run(
            [str(venv_py), "-c", code],
            capture_output=True, text=True, timeout=60, cwd=str(repo),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "YES" in (p.stdout or "")


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
def _already_running(repo: Path) -> bool:
    pidf = dashboard_pid_file(repo)
    if pidf.exists():
        try:
            os.kill(int(pidf.read_text().strip()), 0)
            return True
        except (OSError, ValueError):
            pidf.unlink(missing_ok=True)
    return False


def start_detached(repo: Path, venv_py: Path) -> tuple[bool, str, int | None]:
    """Launch dashboard.api.main detached. Returns (ok, message, pid)."""
    if _already_running(repo):
        return True, "dashboard already running", None

    cfg = dashboard_config(repo, venv_py)
    if port_in_use("127.0.0.1", cfg["port"]):
        ok, msg = health_check(repo, timeout=2.0)
        if ok:
            return True, f"dashboard already serving on port {cfg['port']}", None
        return False, (f"port {cfg['port']} is in use but the dashboard is not "
                       f"healthy: {msg}. Free the port and retry."), None

    log = dashboard_log_file(repo)
    log.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log, "ab")
    try:
        if os.name == "nt":
            proc = subprocess.Popen(
                [str(venv_py), "-m", "dashboard.api.main"],
                stdout=log_fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, cwd=str(repo),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            proc = subprocess.Popen(
                [str(venv_py), "-m", "dashboard.api.main"],
                stdout=log_fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, cwd=str(repo),
                start_new_session=True,
            )
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        return False, f"could not start dashboard: {e}", None

    # Hold the handle for the child's lifetime so output survives rotation.
    proc._log_fh = log_fh  # type: ignore[attr-defined]

    try:
        dashboard_pid_file(repo).parent.mkdir(parents=True, exist_ok=True)
        dashboard_pid_file(repo).write_text(str(proc.pid))
    except OSError:
        pass

    return True, f"dashboard starting (pid={proc.pid}, log={log})", proc.pid