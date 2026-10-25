"""macOS installer wiring for Atlas.

CLI shim: a POSIX wrapper in ~/​.local/bin. Supervisor: native launchd
(LaunchAgent, auto-starts at login + survives reboot with no admin) via the
shared ``setup._supervisor`` -> ``atlas/bin/runtime.py`` path. Falls back to
tmux/nohup when launchd isn't available.
"""
from __future__ import annotations

from pathlib import Path

from ._supervisor import (
    install_startup,
    start_proxy,
    uninstall_runtime,
    verify_startup,
    make_posix_shim,
    service_name,
)

__all__ = [
    "install_cli_shim",
    "install_startup",
    "start_proxy",
    "verify_startup",
    "uninstall_runtime",
    "service_name",
]


def install_cli_shim(repo: Path) -> tuple[bool, str]:
    """Expose `atlas` on the user's PATH (~/​.local/bin), skipping if it already
    exists there to avoid clashing with a pre-existing install."""
    return make_posix_shim(repo, Path.home() / ".local" / "bin")
