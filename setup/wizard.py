"""Simplified Atlas installer wizard.

Flow (one command):
    1. Intro     — one-line explanation
    2. Collect keys — prompt for OpenRouter API key(s) (multiple supported)
    3. Start proxy — launch proxy.main detached, verify /health + keys loaded
    4. Exit      — report success, tell user how to stop

No harness configuration. No auto-start registration. No CLI shims.
Cross-platform: Linux, macOS, Windows.
"""
from __future__ import annotations

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
        "API keys in four steps:\n\n"
        "  1. Bootstrap — check Python, create venv, install dependencies\n"
        "  2. Collect keys — enter your OpenRouter API key(s)\n"
        "  3. Start proxy — launch and verify it is running\n"
        "  4. Exit — you are done\n\n"
        "No harness configuration. No auto-start. No extra steps."
    )
    CONSOLE.print(Panel(Text.from_markup(body), title=title, border_style="cyan"))


def _collect_keys(repo: Path) -> list[str]:
    """Prompt the user for OpenRouter API keys. Returns the full key list."""
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


def _start_and_verify(repo: Path, venv_py: Path, expected_keys: int
                      ) -> tuple[bool, str]:
    """Start the proxy detached and verify it is healthy with keys loaded."""
    ok, msg, _pid = core.start_proxy_detached(repo, venv_py)
    if not ok:
        return False, msg
    CONSOLE.print(f"  [green]✓[/green] {msg}")

    CONSOLE.print("  Waiting for proxy to become healthy...", end="")
    healthy, hmsg = core.health_check(repo, timeout=15.0)
    if not healthy:
        CONSOLE.print(" [red]✘[/red]")
        return False, f"proxy did not become healthy: {hmsg}"
    CONSOLE.print(f" [green]✓[/green] {hmsg}")

    # Confirm keys are present on disk
    actual_keys = core.read_keys(core.key_file(repo))
    if not actual_keys:
        return False, "no OpenRouter keys found in key file"
    if len(actual_keys) < expected_keys:
        return False, (f"expected {expected_keys} key(s), "
                       f"found {len(actual_keys)}")

    return True, (f"proxy running with {len(actual_keys)} "
                  f"OpenRouter key(s)")


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
            CONSOLE.print("    Task Manager → end the python.exe process, or")
            CONSOLE.print("    run:  taskkill /F /IM python.exe /T")
        else:
            CONSOLE.print(f"    kill $(cat {core.pid_file(repo)})")
        CONSOLE.print("\n  To restart later, re-run this installer.")
    else:
        CONSOLE.print(f"\n[red]✗ Setup failed.[/red]")
        log = core.log_file(repo)
        if log.exists():
            CONSOLE.print(f"  Check the proxy log: {log}")


def run_wizard(repo: Path, venv_py: Path) -> int:
    """Run the simplified setup flow. Returns 0 on success, 1 on failure."""
    _intro()

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

    # Write runtime provider config (OpenRouter)
    core.write_runtime_provider(repo, "openrouter")

    # ---- Step 3: Start + verify ----
    CONSOLE.print("\n[cyan]Starting proxy...[/cyan]")
    started_ok, message = _start_and_verify(repo, venv_py, len(keys))

    # ---- Step 4: Summary + exit ----
    _show_summary(repo, added, total, started_ok, message)
    _show_exit(repo, started_ok)

    return 0 if started_ok else 1


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
