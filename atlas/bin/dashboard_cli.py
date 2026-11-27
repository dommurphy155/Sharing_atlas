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


def cmd_change_password(args) -> int:
    """Change the dashboard admin password."""
    import sys
    from rich.prompt import Prompt, Confirm

    CONSOLE.print("[bold]Dashboard — change password[/bold]")
    CONSOLE.print()

    def _read_password(prompt: str) -> str:
        """Read password from stdin - works in all environments."""
        # Try getpass first (for real TTY)
        if sys.stdin.isatty():
            import getpass
            try:
                return getpass.getpass(prompt)
            except (EOFError, OSError, Exception):
                pass
        # Fallback: read directly from stdin with visible prompt
        # In non-TTY environments, we can't hide input anyway
        CONSOLE.print(f"  {prompt.strip()}: ", end="")
        try:
            return sys.stdin.readline().rstrip("\n")
        except Exception:
            return ""

    # Get current password
    current = _read_password("  Current password")
    if not current:
        CONSOLE.print("[red]No password entered.[/red]")
        return 1

    # Get new password
    new1 = _read_password("  New password")
    if not new1:
        CONSOLE.print("[red]No new password entered.[/red]")
        return 1
    if len(new1) < 12:
        CONSOLE.print("[red]Password must be at least 12 characters.[/red]")
        return 1

    new2 = _read_password("  Confirm new password")
    if new1 != new2:
        CONSOLE.print("[red]Passwords do not match.[/red]")
        return 1

    # Confirm
    if not Confirm.ask("  Change password? (revokes all other sessions)", default=False):
        CONSOLE.print("[yellow]Cancelled.[/yellow]")
        return 0

    # Execute via API
    def _run():
        from dashboard.api import db, security
        db.init_db()
        admin = security.get_admin()
        if not admin:
            return False, "No administrator configured"

        ok, _ = security.verify_password(current, admin["password_hash"])
        if not ok:
            return False, "Current password is incorrect"

        ok, msg = security.password_strength_ok(new1)
        if not ok:
            return False, msg

        if security.password_reused(new1):
            return False, "Password was used recently. Choose a different one."

        new_hash = security.hash_password(new1)
        security.record_password_history(admin["password_hash"])
        ts = db.now()
        with db.connect() as conn:
            conn.execute(
                "UPDATE admin SET password_hash = ?, password_changed_at = ? WHERE id = 1",
                (new_hash, ts),
            )

        sid = ""  # no session here; this is a CLI command
        n = security.revoke_all_sessions(except_sid=sid)
        security.audit("password_changed", admin["username"], "cli", f"revoked={n}")
        return True, f"Password changed. Revoked {n} other session(s)."

    result, msg = _with_path(_run)
    if result:
        CONSOLE.print(f"[green]✓ {msg}[/green]")
        return 0
    else:
        CONSOLE.print(f"[red]✘ {msg}[/red]")
        return 1


def cmd_change_port(args) -> int:
    """Change the dashboard listen port (updates .env and restarts if running)."""
    import sys

    def _read_env() -> dict:
        env_file = _repo_root() / ".env"
        if not env_file.exists():
            return {}
        data = {}
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
        return data

    def _write_env(data: dict) -> None:
        env_file = _repo_root() / ".env"
        lines = [f"{k}={v}" for k, v in data.items()]
        env_file.write_text("\n".join(lines) + "\n")

    def _read_input(prompt: str, default: str = "") -> str:
        """Read input from stdin - works in all environments."""
        if sys.stdin.isatty():
            from rich.prompt import Prompt
            return Prompt.ask(prompt, default=default)
        # Fallback: read directly from stdin
        if default:
            CONSOLE.print(f"  {prompt} [{default}]: ", end="")
        else:
            CONSOLE.print(f"  {prompt}: ", end="")
        try:
            val = sys.stdin.readline().rstrip("\n")
            return val if val else default
        except Exception:
            return default

    CONSOLE.print("[bold]Dashboard — change port[/bold]")
    CONSOLE.print()

    # Show current
    env = _read_env()
    current = env.get("DASHBOARD_PORT", "8080")
    CONSOLE.print(f"  Current port: [cyan]{current}[/cyan]")

    new_port = _read_input("  New port", default=current)
    try:
        port = int(new_port)
        if not (1 <= port <= 65535):
            raise ValueError
    except ValueError:
        CONSOLE.print("[red]Invalid port (1-65535).[/red]")
        return 1

    if str(port) == current:
        CONSOLE.print("[yellow]Port unchanged.[/yellow]")
        return 0

    # Update .env
    env["DASHBOARD_PORT"] = str(port)
    _write_env(env)
    CONSOLE.print(f"[green]✓ Updated .env: DASHBOARD_PORT={port}[/green]")

    # If running via systemd, restart
    if _systemd():
        CONSOLE.print("  Restarting dashboard via systemd...")
        rc, out = _systemctl("restart")
        if rc == 0:
            CONSOLE.print("[green]✓ Dashboard restarted with new port[/green]")
        else:
            CONSOLE.print(f"[red]✘ Restart failed: {out}[/red]")
            return 1
    elif _running():
        CONSOLE.print("[yellow]Dashboard is running (pidfile mode). Restart manually:[/yellow]")
        CONSOLE.print("  atlas dashboard stop && atlas dashboard start")
    else:
        CONSOLE.print("[dim]Dashboard not running — new port takes effect on next start.[/dim]")

    return 0


DASHBOARD_SUBCOMMANDS = {
    "start": cmd_start,
    "stop": cmd_stop,
    "restart": cmd_restart,
    "status": cmd_status,
    "logs": cmd_logs,
    "setup-token": cmd_setup_token,
    "change-password": cmd_change_password,
    "change-port": cmd_change_port,
    "set-port": cmd_change_port,
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
