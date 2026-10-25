"""Windows installer wiring for Atlas.

CLI shim: an ``atlas.cmd`` wrapper (Windows resolves ``.cmd`` on PATH). Supervisor:
native Task Scheduler (at-log-on trigger + restart-on-failure, survives reboot
with no admin) via the shared ``setup._supervisor`` -> ``atlas/bin/runtime.py``
path. Falls back to a direct process only when schtasks is unavailable.
"""
from __future__ import annotations

from pathlib import Path

from ._supervisor import (
    install_startup,
    start_proxy,
    uninstall_runtime,
    verify_startup,
    make_windows_shim,
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
    """Expose `atlas` on the user's PATH (~/.local/bin/atlas.cmd), skipping if
    an `atlas` command already exists there to avoid a clash."""
    return make_windows_shim(repo, Path.home() / ".local" / "bin")
