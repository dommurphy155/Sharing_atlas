"""Simplified Atlas installer wizard.

Flow (one command):
    1. Bootstrap   — detect OS, check Python >= 3.10, create venv, install deps
    2. Collect keys — prompt for OpenRouter API key(s), save to key file
    3. Start proxy — launch with a supervisor so it stays alive after exit
    4. Verify      — confirm /health is up and keys are loaded
    5. (Optional)  — install the `atlas` CLI shim for later stop/status
    6. Exit        — report success, tell user how to stop

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
        "  3. Start proxy — launch with a supervisor so it stays alive\n"
        "  4. Verify — confirm it's healthy, then exit\n\n"
        "No harness configuration. No extra steps. The proxy keeps running "
        "after setup finishes."
    )
    CONSOLE.print(Panel(Text.from_markup(body), title=title, border_style="cyan"))


def _clear_windows_key_files(repo):
    """Clear configured OpenRouter key files before collecting fresh keys."""
    import sys
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    paths = [core.key_file(repo)]
    try:
        from proxy import config
        paths.extend((Path(config.KEY_FILE), Path(config.FALLBACK_KEY_FILE)))
    except Exception as exc:
        CONSOLE.print(f"[yellow]Could not load configured key paths: {exc}[/yellow]")

    unique = {}
    for path in paths:
        path = Path(path).expanduser()
        if not path.is_absolute():
            path = repo / path
        path = path.resolve()
        unique[str(path).casefold()] = path

    for path in unique.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    CONSOLE.print(f"[dim]Cleared {len(unique)} configured key file(s).[/dim]")

def _collect_keys(repo: Path) -> list[str]:
    """Prompt the user for OpenRouter API keys. Returns the full key list."""
    if core.is_windows():
        _clear_windows_key_files(repo)
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
                  started_ok: bool, message: str) -> None:
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

    CONSOLE.print()
    CONSOLE.print(tbl)


def _show_exit(repo: Path, started_ok: bool) -> None:
    port = core.proxy_port(repo)
    if started_ok:
        CONSOLE.print(f"\n[green]✓ Setup complete![/green]")
        CONSOLE.print(
            f"  The Atlas proxy is running on http://127.0.0.1:{port}/"
        )
        CONSOLE.print(f"  Log file: {core.log_file(repo)}")
        CONSOLE.print(f"  PID file: {core.pid_file(repo)}")
        CONSOLE.print("\n  To stop the proxy:")
        if core.is_windows():
            CONSOLE.print("    atlas stop")
        else:
            CONSOLE.print(f"    kill $(cat {core.pid_file(repo)})")
        CONSOLE.print("\n  To manage it later, run:  atlas status | atlas stop | atlas start")
        CONSOLE.print("  (if you installed the CLI shim during setup).")
    else:
        CONSOLE.print(f"\n[red]✗ Setup failed.[/red]")
        log = core.log_file(repo)
        if log.exists():
            CONSOLE.print(f"  Check the proxy log: {log}")


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

    # Write runtime provider config (OpenRouter only)
    core.write_runtime_provider(repo, "openrouter")

    # ---- Step 3: Start proxy with supervisor (stays alive) ----
    CONSOLE.print("\n[cyan]Starting proxy (stays alive after setup)...[/cyan]")
    started_ok, message = _start_and_verify(repo, venv_py, len(keys))

    if not started_ok:
        CONSOLE.print("\n[red]✗ Proxy failed to start.[/red]")
        log = core.log_file(repo)
        if log.exists():
            CONSOLE.print(f"  Log tail: {log.read_text()[-500:]}")
        return 1

    # ---- Step 5: Optional CLI shim ----
    CONSOLE.print("\n[cyan]Optional: install `atlas` CLI?[/cyan]")
    CONSOLE.print("  This lets you run `atlas status`, `atlas stop`, `atlas start` later.")
    if core.is_windows() or Confirm.ask("Install CLI shim?", default=True):
        shim_ok, shim_msg = _install_cli_shim(repo, osmod)
        shim_style = "[green]✓[/green]" if shim_ok else "[red]✘[/red]"
        CONSOLE.print(f"  {shim_style} {shim_msg}")

    # ---- Step 6: Summary + exit ----
    _show_summary(repo, added, total, True, message)
    _show_exit(repo, True)

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
