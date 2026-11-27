"""Simplified Atlas installer wizard.

Flow (one command):
    1. Bootstrap   — detect OS, check Python >= 3.10, create venv, install deps
    2. Collect keys — prompt for OpenRouter API key(s), save to key file
    3. Start proxy — launch with a supervisor so it stays alive after exit
    4. Verify      — confirm /health is up and keys are loaded
    5. (Optional)  — install the `atlas` CLI shim for later stop/status
    6. (Optional)  — set the dashboard password and start the dashboard
    7. Exit        — report success, tell user how to stop

No harness configuration. No provider choice (OpenRouter only). No extra steps.
The proxy keeps running after setup finishes.
Cross-platform: Linux, macOS, Windows.
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

import setup.core as core

CONSOLE = Console()


def _intro() -> None:
    title = "[bold underline]Atlas Proxy Setup[/bold underline]"
    body = (
        "This gets the Atlas translation proxy running with your OpenRouter "
        "API keys. Four steps:\n\n"
        "  1. Bootstrap — check Python, create venv, install dependencies\n"
        "  2. Collect keys — enter your OpenRouter API key(s)\n"
        "  3. Configure ports — choose proxy + dashboard listen ports\n"
        "  4. Start proxy — launch with a supervisor so it stays alive\n"
        "  5. Verify — confirm it's healthy, then exit\n\n"
        "You'll then be offered the optional dashboard: set an admin password "
        "and it starts alongside the proxy.\n\n"
        "No harness configuration. No extra steps. The proxy keeps running "
        "after setup finishes."
    )
    CONSOLE.print(Panel(Text.from_markup(body), title=title, border_style="cyan"))


def _collect_keys(repo: Path) -> list[str]:
    """Prompt the user for OpenRouter API keys. Returns the full key list."""
    # Preserve existing keys on every platform; collection handles additions.
    target = core.key_file(repo)
    existing = core.read_keys(target)

    CONSOLE.print("\n[cyan]OpenRouter API keys[/cyan]")
    if existing:
        CONSOLE.print(
            f"  [dim]{len(existing)} key(s) already saved in {target}[/dim]"
        )
        if not Confirm.ask("Add more keys?", default=False):
            return existing

    CONSOLE.print("  Enter your OpenRouter API key(s), one per line.")
    CONSOLE.print("  Press Enter on a blank line to finish.\n")

    keys = list(existing)
    while True:
        key = Prompt.ask("API key", default="").strip()
        if not key:
            break
        if key in keys:
            CONSOLE.print("  [yellow]already saved[/yellow]")
            continue
        if not core.key_prefix_ok(key):
            CONSOLE.print(
                "  [yellow]⚠ warning: does not look like an OpenRouter key "
                "(expected 'sk-or-' prefix) — saving anyway[/yellow]"
            )
        keys.append(key)
        CONSOLE.print("  [green]✓ added[/green]")

    return keys


def _save_keys(repo: Path, keys: list[str]) -> tuple[int, int]:
    """Save keys, return (newly_added, total_on_disk)."""
    target = core.key_file(repo)
    added = core.append_keys(target, keys)
    total = len(core.read_keys(target))
    return added, total


def _configure_ports(repo: Path) -> dict:
    """Prompt the user for proxy and dashboard ports. Returns dict with ports."""
    import os

    # Read existing .env for current values
    env_file = repo / ".env"
    current_proxy_port = "8788"
    current_dash_port = "8080"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("LISTEN_PORT="):
                current_proxy_port = line.split("=", 1)[1]
            elif line.startswith("DASHBOARD_PORT="):
                current_dash_port = line.split("=", 1)[1]

    CONSOLE.print("\n[cyan]Port configuration[/cyan]")
    CONSOLE.print("  Enter ports for proxy and dashboard (1-65535).")
    CONSOLE.print("  Press Enter to keep current values.\n")

    proxy_port = Prompt.ask("  Proxy port", default=current_proxy_port)
    dash_port = Prompt.ask("  Dashboard port", default=current_dash_port)

    # Validate
    try:
        proxy_port = int(proxy_port)
        dash_port = int(dash_port)
        if not (1 <= proxy_port <= 65535) or not (1 <= dash_port <= 65535):
            raise ValueError
    except ValueError:
        CONSOLE.print("[red]Invalid port (1-65535). Using defaults.[/red]")
        proxy_port = 8788
        dash_port = 8080

    if proxy_port == dash_port:
        CONSOLE.print("[yellow]Ports cannot be the same. Using defaults.[/yellow]")
        proxy_port = 8788
        dash_port = 8080

    # Write to .env
    env_data = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_data[k.strip()] = v.strip()

    env_data["LISTEN_PORT"] = str(proxy_port)
    env_data["DASHBOARD_PORT"] = str(dash_port)

    # Update CORS_ORIGINS to include dashboard port
    cors = env_data.get("CORS_ORIGINS", "http://127.0.0.1:8080,http://localhost:8080")
    new_cors = []
    for origin in cors.split(","):
        origin = origin.strip()
        if origin:
            # Replace dashboard port in origin if it matches old dash port
            new_origin = origin.replace(f":{current_dash_port}", f":{dash_port}")
            new_cors.append(new_origin)
    env_data["CORS_ORIGINS"] = ",".join(new_cors)

    env_file.write_text("\n".join(f"{k}={v}" for k, v in env_data.items()) + "\n")
    os.environ["LISTEN_PORT"] = str(proxy_port)
    os.environ["DASHBOARD_PORT"] = str(dash_port)

    CONSOLE.print(f"  [green]✓[/green] Proxy port: {proxy_port}")
    CONSOLE.print(f"  [green]✓[/green] Dashboard port: {dash_port}")
    CONSOLE.print(f"  [dim]CORS updated to include dashboard port {dash_port}[/dim]")

    return {"proxy_port": proxy_port, "dash_port": dash_port}


def _os_module():
    """Lazily import the OS-specific setup module (linux_setup / mac_setup / windows_setup)."""
    name = core.detect_os()
    return importlib.import_module(f".{name}_setup", package="setup")


def _start_with_supervisor(repo: Path, osmod) -> tuple[bool, str]:
    """Start the proxy via the OS supervisor so it stays alive after exit.

    Delegates to setup._supervisor.start_proxy -> runtime.install_runtime,
    which picks the best available supervisor (systemd / launchd /
    scheduled-task / tmux / nohup), starts the proxy, health-checks it, and
    persists the choice for later `atlas start/stop/status`.
    """
    try:
        ok, msg = osmod.start_proxy(repo)
    except Exception as e:  # never crash the installer on a supervisor error
        return False, f"supervisor start failed: {type(e).__name__}: {e}"
    return ok, msg


def _install_cli_shim(repo: Path, osmod) -> tuple[bool, str]:
    """Install the `atlas` CLI wrapper on PATH so the user can manage the proxy."""
    try:
        return osmod.install_cli_shim(repo)
    except Exception as e:
        return False, f"CLI shim install failed: {type(e).__name__}: {e}"


def _verify(repo: Path, expected_keys: int) -> tuple[bool, str]:
    """Verify the proxy is healthy and keys are loaded."""
    healthy, hmsg = core.health_check(repo, timeout=15.0)
    if not healthy:
        return False, f"proxy not healthy: {hmsg}"

    actual_keys = core.read_keys(core.key_file(repo))
    if not actual_keys:
        return False, "no OpenRouter keys found in key file"
    if len(actual_keys) < expected_keys:
        return False, f"expected {expected_keys} key(s), found {len(actual_keys)}"

    port = core.proxy_port(repo)
    return True, f"healthy on 127.0.0.1:{port} with {len(actual_keys)} key(s)"


def _start_and_verify(repo: Path, venv_py: Path, expected_keys: int
                      ) -> tuple[bool, str]:
    """Start the proxy with a supervisor and verify it is healthy with keys loaded."""
    osmod = _os_module()
    ok, msg = _start_with_supervisor(repo, osmod)
    if not ok:
        return False, msg

    CONSOLE.print("  Waiting for proxy to become healthy...", end="")
    ok, vmsg = _verify(repo, expected_keys)
    if not ok:
        CONSOLE.print(f" [red]✘[/red]")
        return False, vmsg
    CONSOLE.print(f" [green]✓[/green] {vmsg}")

    return True, vmsg


def _show_summary(repo: Path, added: int, total: int,
                  started_ok: bool, message: str,
                  shim_ok: bool | None = None,
                  shim_msg: str = "",
                  dash: dict | None = None) -> None:
    tbl = Table(show_header=True, header_style="bold cyan",
                box=None, padding=(0, 1))
    tbl.add_column("Step", style="bold")
    tbl.add_column("Status", justify="center", width=8)
    tbl.add_column("Detail", style="dim", overflow="fold")

    port = core.proxy_port(repo)
    kf = core.key_file(repo)

    tbl.add_row("Bootstrap (venv + deps)", "[green]✓[/green]",
                "virtualenv ready")
    tbl.add_row("Keys saved", "[green]✓[/green]",
                f"{added} new, {total} total in {kf.name}")
    started_style = "[green]✓[/green]" if started_ok else "[red]✘[/red]"
    tbl.add_row("Proxy started", started_style, message)
    tbl.add_row(
        "Health check",
        started_style,
        f"listening on 127.0.0.1:{port}" if started_ok else "failed",
    )
    tbl.add_row(
        "Keys loaded",
        started_style,
        f"{total} OpenRouter key(s)" if started_ok else "not verified",
    )
    # Report the CLI's real state. 'skipped' means the user declined; a
    # failure is a failure even when the proxy is healthy.
    if shim_ok is None:
        cli_style, cli_detail = "[yellow]—[/yellow]", shim_msg or "skipped"
    elif shim_ok:
        cli_style, cli_detail = "[green]✓[/green]", shim_msg
    else:
        cli_style, cli_detail = "[red]✘[/red]", shim_msg
    tbl.add_row("CLI (`atlas`)", cli_style, cli_detail)

    # Dashboard row: only shown when the user asked for it.
    if dash and dash.get("wanted"):
        if dash.get("ok"):
            d_style, d_detail = "[green]✓[/green]", dash.get("detail", "")
        else:
            d_style, d_detail = "[red]✘[/red]", dash.get("detail", "failed")
        tbl.add_row("Dashboard", d_style, d_detail)

    CONSOLE.print()
    CONSOLE.print(tbl)


def _show_exit(repo: Path, started_ok: bool,
               shim_ok: bool | None = None,
               dash: dict | None = None) -> None:
    port = core.proxy_port(repo)
    if started_ok:
        CONSOLE.print(f"\n[green]✓ Setup complete![/green]")
        CONSOLE.print(
            f"  The Atlas proxy is running on http://127.0.0.1:{port}/"
        )
        CONSOLE.print(f"  Log file: {core.log_file(repo)}")
        CONSOLE.print(f"  PID file: {core.pid_file(repo)}")
        if shim_ok:
            CONSOLE.print("\n  To stop the proxy:")
            if core.is_windows():
                CONSOLE.print("    atlas stop")
            else:
                CONSOLE.print(f"    kill $(cat {core.pid_file(repo)})")
            CONSOLE.print("\n  To manage it later, run:  atlas status | atlas stop | atlas start")
        else:
            # Don't advertise a command that isn't on PATH.
            CONSOLE.print("\n  To stop the proxy:")
            if core.is_windows():
                CONSOLE.print(f"    taskkill /PID <pid> /T /F   (pid in {core.pid_file(repo)})")
            else:
                CONSOLE.print(f"    kill $(cat {core.pid_file(repo)})")
            CONSOLE.print(f"\n  CLI not on PATH — invoke it directly:")
            CONSOLE.print(f"    {core.venv_python(repo)} {repo / 'atlas' / 'bin' / 'atlas'} status")
        if dash and dash.get("ok"):
            from . import dashboard_setup as _dash
            cfg = _dash.dashboard_config(repo, core.venv_python(repo))
            CONSOLE.print(
                f"\n  Dashboard: http://{cfg['host']}:{cfg['port']}"
                f"  (log: {_dash.dashboard_log_file(repo)})"
            )
    else:
        CONSOLE.print(f"\n[red]✗ Setup failed.[/red]")
        log = core.log_file(repo)
        if log.exists():
            CONSOLE.print(f"  Check the proxy log: {log}")


def _prompt_password(prompt: str) -> str:
    """Read a password without echoing it.

    getpass falls back to a visible prompt when no TTY is available (some CI
    and container consoles); the wizard is interactive anyway, so warn once.
    """
    import getpass
    try:
        return getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""
    except Exception:
        return Prompt.ask(prompt, password=True)


def _dashboard_section(repo: Path, venv_py: Path) -> dict:
    """Optional step: create the admin password and start the dashboard.

    Returns a summary dict for the final table. Never raises — a failure here
    must not fail an otherwise successful proxy install.
    """
    from . import dashboard_setup as dash

    result: dict = {"wanted": False, "ok": False, "detail": "skipped"}

    try:
        if not Confirm.ask("\nStart the management dashboard too?", default=True):
            return result
    except (EOFError, KeyboardInterrupt):
        return result

    result["wanted"] = True

    # ---- Password ----
    try:
        if dash.admin_exists(repo, venv_py):
            CONSOLE.print("[yellow]An admin already exists — keeping it.[/yellow]")
        else:
            CONSOLE.print("\n[cyan]Set the dashboard password.[/cyan]")
            CONSOLE.print("  [dim]12+ characters, upper, lower, number and symbol.[/dim]")
            pw1 = _prompt_password("  Password: ")
            if not pw1:
                result["detail"] = "no password entered"
                CONSOLE.print("[red]✘ No password entered — skipping dashboard.[/red]")
                return result
            pw2 = _prompt_password("  Confirm:  ")
            if pw1 != pw2:
                result["detail"] = "passwords did not match"
                CONSOLE.print("[red]✘ Passwords did not match — skipping dashboard.[/red]")
                return result

            sys.path.insert(0, str(repo))
            try:
                from dashboard.api.security import password_strength_ok  # type: ignore
                ok, msg = password_strength_ok(pw1)
            except Exception:
                ok, msg = True, "ok"
            finally:
                try:
                    sys.path.remove(str(repo))
                except ValueError:
                    pass
            if not ok:
                result["detail"] = msg
                CONSOLE.print(f"[red]✘ {msg}[/red]")
                return result

            ok, msg = dash._create_admin(repo, venv_py, pw1)
            if not ok:
                result["detail"] = msg
                CONSOLE.print(f"[red]✘ Could not create the admin: {msg}[/red]")
                return result
            CONSOLE.print("[green]✓[/green] Admin password set")
    except (EOFError, KeyboardInterrupt):
        result["detail"] = "cancelled"
        return result

    # ---- Start ----
    CONSOLE.print("\n[cyan]Starting dashboard...[/cyan]")
    try:
        ok, msg, pid = dash.start_detached(repo, venv_py)
    except Exception as e:  # never let this break the install
        result["detail"] = f"start failed: {e}"
        CONSOLE.print(f"[red]✘ {result['detail']}[/red]")
        return result

    if not ok:
        result["detail"] = msg
        CONSOLE.print(f"[red]✘ {msg}[/red]")
        return result

    healthy, hmsg = dash.health_check(repo, timeout=25.0)
    result["ok"] = healthy
    result["detail"] = hmsg
    if healthy:
        cfg = dash.dashboard_config(repo, venv_py)
        CONSOLE.print(f"[green]✓[/green] Dashboard up — http://{cfg['host']}:{cfg['port']}")
    else:
        CONSOLE.print(f"[red]✘ Dashboard did not become healthy: {hmsg}[/red]")
        log = dash.dashboard_log_file(repo)
        if log.exists():
            CONSOLE.print(f"[dim]  log: {log}[/dim]")
    return result


def run_wizard(repo: Path, venv_py: Path) -> int:
    """Run the simplified setup flow. Returns 0 on success, 1 on failure."""
    _intro()

    osmod = _os_module()

    # ---- Step 2: Collect keys ----
    keys = _collect_keys(repo)
    if not keys:
        CONSOLE.print("\n[red]✘ No keys provided — cannot start the proxy "
                      "without OpenRouter keys.[/red]")
        CONSOLE.print("  Re-run the installer and enter at least one key.")
        return 1

    added, total = _save_keys(repo, keys)
    CONSOLE.print(f"\n[green]✓[/green] Saved {added} new key(s) "
                  f"({total} total) to {core.key_file(repo)}")

    # ---- Step 3: Configure ports ----
    _configure_ports(repo)

    # Write runtime provider config (OpenRouter only)
    core.write_runtime_provider(repo, "openrouter")

    # ---- Step 4: Start proxy with supervisor (stays alive) ----
    CONSOLE.print("\n[cyan]Starting proxy (stays alive after setup)...[/cyan]")
    started_ok, message = _start_and_verify(repo, venv_py, len(keys))

    if not started_ok:
        CONSOLE.print("\n[red]✗ Proxy failed to start.[/red]")
        log = core.log_file(repo)
        if log.exists():
            CONSOLE.print(f"  Log tail: {log.read_text(encoding='utf-8', errors='replace')[-500:]}", markup=False, highlight=False)
        return 1

    # ---- Step 5: Optional CLI shim ----
    CONSOLE.print("\n[cyan]Optional: install `atlas` CLI?[/cyan]")
    CONSOLE.print("  This lets you run `atlas status`, `atlas stop`, `atlas start` later.")
    shim_ok: bool | None = None
    shim_msg = "skipped"
    if core.is_windows() or Confirm.ask("Install CLI shim?", default=True):
        shim_ok, shim_msg = _install_cli_shim(repo, osmod)
        shim_style = "[green]✓[/green]" if shim_ok else "[red]✘[/red]"
        CONSOLE.print(f"  {shim_style} {shim_msg}")
        # A failed CLI registration is not a failed proxy install. The proxy is
        # already up and verified; report the shim state and let the user decide
        # whether to fix PATH or use the venv python directly.
        if not shim_ok:
            CONSOLE.print("[yellow]The Atlas proxy is running; only the 'atlas' "
                          "convenience command is unavailable.[/yellow]")
            CONSOLE.print(f"[dim]  Invoke the CLI directly: "
                          f"{core.venv_python(repo)} {repo / 'atlas' / 'bin' / 'atlas'}[/dim]")
            if core.is_windows():
                CONSOLE.print(f"[dim]  Or add {Path.home() / '.local' / 'bin'} to your "
                              f"user PATH, then open a new shell.[/dim]")
    else:
        CONSOLE.print("[dim]  Skipped; proxy management still works via the runtime "
                      "and the venv python.[/dim]")

    # ---- Step 5b: Optional dashboard ----
    dash_result = _dashboard_section(repo, venv_py)

    # ---- Step 6: Summary + exit ----
    _show_summary(repo, added, total, True, message, shim_ok, shim_msg, dash_result)
    _show_exit(repo, True, shim_ok, dash_result)

    return 0


def main() -> int:
    # --help / -h regression guard (used by install.sh / install.ps1 to verify
    # the module entry point is runnable before handing off).
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Atlas Proxy Setup")
        print()
        print("Usage:  python -m setup.wizard")
        print()
        print("Flow: bootstrap -> enter OpenRouter keys -> start proxy -> verify -> exit.")
        print("Run interactively; no arguments required.")
        return 0

    repo = core.repo_root()
    CONSOLE.print(f"[dim]repo: {repo}[/dim]")
    CONSOLE.print(f"[dim]os: {core.detect_os()}[/dim]")

    # ---- Step 1: Bootstrap ----
    try:
        venv_py = core.bootstrap(repo)
    except RuntimeError as e:
        CONSOLE.print(f"[red]✗ Bootstrap failed: {e}[/red]")
        return 1
    CONSOLE.print("[green]✓[/green] Environment ready")

    return run_wizard(repo, venv_py)


if __name__ == "__main__":
    sys.exit(main())
