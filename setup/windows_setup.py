
"""Windows-specific Atlas setup helpers."""

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
    """Install the Atlas CLI wrapper in the user's local bin directory."""
    bin_dir = Path.home() / ".local" / "bin"
    return make_windows_shim(repo, bin_dir)
