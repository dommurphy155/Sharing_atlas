"""atlas dashboard — CLI commands for the dashboard (lifecycle, service, setup).

Imported by atlas/bin/atlas. Kept separate so the core CLI stays untouched.

The dashboard ships as a systemd unit (atlas-dashboard.service). These commands
drive systemd when it is present and fall back to a pidfile when it is not, so
the CLI works on a bare install too. The proxy's own supervisor is never
assumed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

CONSOLE = Console()

SERVICE = "atlas-dashboard.service"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _venv_py() -> Path:
    p = _repo_root() / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return p if p.exists() else Path(sys.executable)


def _with_path(fn):
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    return fn()


def _systemd() -> bool:
    """True when systemd is PID 1 and systemctl is usable."""
    if not shutil.which("systemctl"):
        return False
    try:
        return Path("/proc/1/comm").read_text().strip() == "systemd"
    except OSError:
        return False


def _systemctl(action: str) -> tuple[int, str]:
    p = subprocess.run(["systemctl", action, SERVICE],
                       capture_output=True, text=True, timeout=30)
    return p.returncode, (p.stdout + p.stderr).strip()


def _dashboard_pidfile() -> Path:
    return _repo_root() / "data" / "dashboard.pid"


def _running() -> bool:
    """Is the dashboard up? systemd first, pidfile second."""
    if _systemd():
        rc, out = _systemctl("show")
        if rc == 0:
            for line in out.splitlines():
                if line.startswith("ActiveState="):
                    return line.split("=", 1)[1].strip() == "active"
    pidf = _dashboard_pidfile()
    if pidf.exists():
        try:
            os.kill(int(pidf.read_text().strip()), 0)
            return True
        except (OSError, ValueError):
            pidf.unlink(missing_ok=True)
    return False


def cmd_setup_token(args) -> int:
    """Mint a one-time setup token for first-run admin creation.

    Printed to the local terminal only, valid for a short window, single use.
    """
    def _run():
        from dashboard.api import db, security
        db.init_db()
        if security.admin_exists():
            return None, "The dashboard already has an administrator."
        return security.mint_setup_token(), ""

    token, err = _with_path(_run)
    if err:
        CONSOLE.print(f"[yellow]{err}[/yellow]")
        return 0
    CONSOLE.print("[bold]Setup token[/bold] (valid 15 minutes, single use):")
    CONSOLE.print(f"  [green]{token}[/green]")
    CONSOLE.print("[dim]Post it to /api/setup/complete with your chosen password.[/dim]")
    return 0


def cmd_start(args) -> int:
    if _running():
        CONSOLE.print("[green]Dashboard already running.[/green]")
        return 0
    if _systemd():
        rc, out = _systemctl("start")
        CONSOLE.print(f"[green]Dashboard started via systemd.[/green]" if rc == 0
                      else f"[red]{out}[/red]")
        return rc
    pid = _dashboard_pidfile()
    log = _repo_root() / "data" / "dashboard.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as fh:
        proc = subprocess.Popen(
            [str(_venv_py()), "-m", "dashboard.api.main"],
            cwd=str(_repo_root()), stdout=fh, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    pid.write_text(str(proc.pid))
    CONSOLE.print(f"[green]Dashboard started (pid={proc.pid}).[/green]  log={log}")
    return 0


def cmd_stop(args) -> int:
    if _systemd():
        rc, out = _systemctl("stop")
        CONSOLE.print("[green]Dashboard stopped.[/green]" if rc == 0 else f"[red]{out}[/red]")
        return rc
    pidf = _dashboard_pidfile()
    if not pidf.exists():
        CONSOLE.print("[yellow]Dashboard not running.[/yellow]")
        return 0
    try:
        os.kill(int(pidf.read_text().strip()), 15)
    except (OSError, ValueError):
        pass
    pidf.unlink(missing_ok=True)
    CONSOLE.print("[green]Dashboard stopped.[/green]")
    return 0


def cmd_restart(args) -> int:
    if _systemd():
        rc, out = _systemctl("restart")
        CONSOLE.print("[green]Dashboard restarted.[/green]" if rc == 0 else f"[red]{out}[/red]")
        return rc
    cmd_stop(args)
    return cmd_start(args)


def cmd_status(args) -> int:
    def _run():
        from dashboard.api.config import APP_HOST, APP_PORT
        return APP_HOST, APP_PORT
    host, port = _with_path(_run)
    running = _running()
    CONSOLE.print(f"Dashboard: {'[green]running[/green]' if running else '[yellow]stopped[/yellow]'}")
    if _systemd():
        rc, out = _systemctl("show")
        for line in out.splitlines():
            if line.startswith(("ActiveState=", "SubState=", "MainPID=")):
                CONSOLE.print(f"  {line}")
    CONSOLE.print(f"  internal: http://{host}:{port}")
    return 0 if running else 1


def cmd_logs(args) -> int:
    """Tail the dashboard's own log."""
    if _systemd():
        subprocess.run(["journalctl", "-u", SERVICE, "-n", "60", "--no-pager"])
        return 0
    p = _repo_root() / "data" / "dashboard.log"
    if not p.exists():
        CONSOLE.print("[yellow]No dashboard log yet.[/yellow]")
        return 1
    lines = p.read_text(errors="replace").splitlines()[-60:]
    for ln in lines:
        CONSOLE.print(ln)
    return 0


DASHBOARD_SUBCOMMANDS = {
    "start": cmd_start,
    "stop": cmd_stop,
    "restart": cmd_restart,
    "status": cmd_status,
    "logs": cmd_logs,
    "setup-token": cmd_setup_token,
}


def dispatch(args) -> int:
    sub = getattr(args, "dashboard_command", None)
    if not sub:
        CONSOLE.print("[bold]atlas dashboard[/bold] <start|stop|restart|status|logs>")
        return 0
    handler = DASHBOARD_SUBCOMMANDS.get(sub)
    if not handler:
        CONSOLE.print(f"[red]Unknown dashboard command: {sub}[/red]")
        return 2
    return handler(args)
