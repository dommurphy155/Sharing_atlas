"""Linux installer wiring for Atlas.

Only the CLI-shim is Linux-specific; supervisor install/start/verify/uninstall
and the key files live in the shared ``setup._supervisor`` module (which drives
``atlas/bin/runtime.py`` — auto-picking systemd / systemd-user / tmux / nohup).
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
    """Expose `atlas` on the user's PATH (~/​local/bin), skipping if it already
    exists there to avoid clashing with a pre-existing install."""
    return make_posix_shim(repo, Path.home() / ".local" / "bin")
