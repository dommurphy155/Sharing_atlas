
"""Shared runtime and CLI-shim helpers for Atlas setup."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

import setup.core as core


def service_name(repo: Path) -> str:
    """Return the configured Atlas runtime identity."""
    return os.environ.get("ATLAS_SERVICE_NAME", core.SERVICE_NAME)


def _atlas_on_path() -> str | None:
    """Return an existing Atlas command, if one is available."""
    return shutil.which("atlas")


def _load_runtime(repo: Path):
    """Load the runtime module from this repository, avoiding repeated paths."""
    atlas_dir = str((repo / "atlas").resolve())

    if atlas_dir not in sys.path:
        sys.path.insert(0, atlas_dir)

    import importlib

    return importlib.import_module("bin.runtime")


def start_proxy(repo: Path) -> tuple[bool, str]:
    """Start the proxy through the existing runtime implementation."""
    try:
        rt = _load_runtime(repo)
        venv_py = core.venv_python(repo)
        mode, ok, message = rt.install_runtime(
            repo, venv_py, service_name(repo)
        )
        return bool(ok), f"{mode}: {message}"
    except Exception as exc:
        return False, f"Runtime start failed: {type(exc).__name__}: {exc}"


def install_startup(repo: Path) -> tuple[bool, str]:
    """Start and verify the runtime without adding another supervisor."""
    return start_proxy(repo)


def verify_startup(repo: Path) -> tuple[bool, str]:
    """Check the status of the configured runtime."""
    try:
        rt = _load_runtime(repo)
        venv_py = core.venv_python(repo)
        runtime = rt.get_runtime(repo, venv_py, service_name(repo))
        return runtime.status()
    except Exception as exc:
        return False, f"Runtime status failed: {type(exc).__name__}: {exc}"


def uninstall_runtime(repo: Path) -> tuple[bool, str]:
    """Stop and remove the runtime configuration, if present."""
    try:
        rt = _load_runtime(repo)
        venv_py = core.venv_python(repo)
        runtime = rt.get_runtime(repo, venv_py, service_name(repo))
        runtime.cleanup()
        return True, "Runtime stopped and configuration removed."
    except Exception as exc:
        return False, f"Runtime cleanup failed: {type(exc).__name__}: {exc}"


def _shim_header() -> str:
    return "Atlas CLI - installed by the cross-platform installer."


def make_posix_shim(repo: Path, bin_dir: Path) -> tuple[bool, str]:
    """Install the POSIX CLI wrapper without overwriting an existing command."""
    existing = _atlas_on_path()

    if existing:
        return True, f"Atlas command already exists at {existing}; skipped."

    python = core.venv_python(repo)
    target = repo / "atlas" / "bin" / "atlas"

    if not Path(python).is_file():
        return False, f"Virtual-environment Python not found: {python}"

    if not target.is_file():
        return False, f"Atlas CLI entry point not found: {target}"

    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "atlas"

    shim.write_text(
        "#!/bin/sh\n"
        f"# {_shim_header()}\n"
        f'exec "{python}" "{target}" "$@"\n',
        encoding="utf-8",
    )

    _chmodx(shim)

    on_path = str(bin_dir) in os.environ.get("PATH", "").split(os.pathsep)
    hint = "" if on_path else f" Add {bin_dir} to PATH to run atlas globally."

    return True, f"Installed CLI wrapper: {shim}.{hint}"


def make_windows_shim(repo: Path, bin_dir: Path) -> tuple[bool, str]:
    """Install the Windows CLI wrapper."""
    python = Path(core.venv_python(repo))
    target = repo / "atlas" / "bin" / "atlas"

    if not python.is_file():
        return False, f"Virtual-environment Python not found: {python}"

    if not target.is_file():
        return False, f"Atlas CLI entry point not found: {target}"

    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "atlas.cmd"

    # Do not overwrite unrelated commands. Only skip when the existing
    # command resolves to this exact wrapper.
    existing = _atlas_on_path()

    if existing:
        try:
            if Path(existing).resolve() == shim.resolve():
                return True, f"Atlas CLI wrapper already installed: {shim}"
        except (OSError, ValueError):
            pass

    shim.write_text(
        "@echo off\r\n"
        f"rem {_shim_header()}\r\n"
        f'"{python}" "{target}" %*\r\n'
        "exit /b %ERRORLEVEL%\r\n",
        encoding="utf-8",
    )

    path_entries = [
        os.path.normcase(os.path.normpath(entry.strip('"')))
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry
    ]
    normalized_bin = os.path.normcase(os.path.normpath(str(bin_dir)))
    on_path = normalized_bin in path_entries

    hint = ""
    if not on_path:
        hint = (
            f" Add {bin_dir} to your user PATH, then reopen the terminal."
        )

    return True, f"Installed CLI wrapper: {shim}.{hint}"


def _chmodx(path: Path) -> None:
    """Ensure the POSIX wrapper is executable."""
    mode = path.stat().st_mode
    path.chmod(
        mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )


__all__ = [
    "service_name",
    "start_proxy",
    "install_startup",
    "verify_startup",
    "uninstall_runtime",
    "make_posix_shim",
    "make_windows_shim",
]
