"""Shared supervisor + CLI-shim helpers for the OS-specific installer wizards.

Pure-stdlib (atlas/bin/runtime.py is stdlib-only too) so these helpers work on
a fresh install *before* the proxy's third-party deps are present. The three
`setup/{os}_setup.py` modules import from here and only add their OS-specific
CLI-shim behaviour, keeping shared logic in one place.
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

import setup.core as core


# ---------------------------------------------------------------------------
# Service identity
# ---------------------------------------------------------------------------
def service_name(repo: Path) -> str:
    """Systemd/launchd/scheduled-task unit name.

    Honours ATLAS_SERVICE_NAME (matches the atlas CLI's own convention) so the
    unit the installer registers and the one the atlas CLI drives are identical.
    """
    return os.environ.get("ATLAS_SERVICE_NAME", core.SERVICE_NAME)


def _atlas_on_path() -> str | None:
    """Absolute path to an existing `atlas` CLI on PATH, or None."""
    return shutil.which("atlas")


# ---------------------------------------------------------------------------
# Runtime install / start / verify / uninstall (delegated to runtime.py)
# ---------------------------------------------------------------------------
def _load_runtime(repo: Path):
    """Import atlas/bin/runtime.py from this repo copy."""
    atlas_dir = repo / "atlas"
    sys.path.insert(0, str(atlas_dir))
    from bin import runtime as rt  # type: ignore
    return rt


def start_proxy(repo: Path) -> tuple[bool, str]:
    """Install + start + health-verify the proxy runtime (idempotent).

    Delegates to ``runtime.install_runtime``, which picks the best available
    supervisor (systemd / systemd-user / launchd / scheduled-task / tmux /
    nohup), installs + starts it, health-checks /health, and persists the
    choice to data/runtime.json for subsequent ``atlas start/status`` calls.
    """
    rt = _load_runtime(repo)
    venv_py = core.venv_python(repo)
    try:
        mode, ok, msg = rt.install_runtime(repo, venv_py, service_name(repo))
    except Exception as e:  # never crash the installer on a runtime error
        return False, f"runtime install failed: {type(e).__name__}: {e}"
    return ok, f"{mode}: {msg}"


def install_startup(repo: Path) -> tuple[bool, str]:
    """Register auto-start + start the proxy (same call both installs and
    verifies the supervisor, so a single invocation covers both)."""
    return start_proxy(repo)


def verify_startup(repo: Path) -> tuple[bool, str]:
    """Report whether the chosen runtime is currently running/loaded."""
    rt = _load_runtime(repo)
    venv_py = core.venv_python(repo)
    try:
        runtime = rt.get_runtime(repo, venv_py, service_name(repo))
        return runtime.status()
    except Exception as e:
        return False, f"runtime status failed: {type(e).__name__}: {e}"


def uninstall_runtime(repo: Path) -> tuple[bool, str]:
    """Best-effort: stop + remove the registered supervisor config."""
    rt = _load_runtime(repo)
    venv_py = core.venv_python(repo)
    try:
        runtime = rt.get_runtime(repo, venv_py, service_name(repo))
        runtime.cleanup()
        return True, "runtime stopped + config removed"
    except Exception as e:
        return False, f"cleanup failed: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# CLI shim helpers (OS-specific wrappers differ; logic shared)
# ---------------------------------------------------------------------------
def _shim_header() -> str:
    return "# Atlas CLI — installed by the Atlas cross-platform installer."

def make_posix_shim(repo: Path, bin_dir: Path) -> tuple[bool, str]:
    """Write a tiny `atlas` wrapper into bin_dir (POSIX, Linux + macOS).

    Uses a hard-coded exec of the install's venv python on atlas/bin/atlas —
    more robust than a symlink (no permissions dance, never picks the wrong
    repo). Skips when `atlas` already exists on PATH to avoid clashing with a
    pre-existing install (per user: do not clobber an existing symlink).
    """
    existing = _atlas_on_path()
    if existing:
        return True, f"'atlas' already on PATH at {existing} — skipped (no clash)"
    py = core.venv_python(repo)
    target = repo / "atlas" / "bin" / "atlas"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "atlas"
    shim.write_text(
        f"#!{'/bin/sh' if core.is_posix() else '/bin/sh'}\n"
        f"# {_shim_header()}\n"
        f'exec "{py}" "{target}" "$@"\n'
    )
    _chmodx(shim)
    on_path = str(bin_dir) in os.environ.get("PATH", "")
    hint = "" if on_path else f"  (later: add {bin_dir} to your PATH)"
    return True, f"installed CLI wrapper at {shim}{hint}"


def make_windows_shim(repo: Path, bin_dir: Path) -> tuple[bool, str]:
    """Write an `atlas.cmd` wrapper into bin_dir (Windows).

    Skips when `atlas` already exists on PATH (no symlink clash).
    """
    existing = _atlas_on_path()
    if existing:
        return True, f"'atlas' already on PATH at {existing} — skipped (no clash)"
    py = core.venv_python(repo)        # .../.venv/Scripts/python.exe
    target = repo / "atlas" / "bin" / "atlas"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "atlas.cmd"
    shim.write_text(
        "@echo off\n"
        f"rem {_shim_header()}\n"
        f'"{py}" "{target}" %*\n'
    )
    on_path = str(bin_dir) in os.environ.get("PATH", "")
    hint = "" if on_path else f"  (later: add {bin_dir} to your PATH)"
    return True, f"installed CLI wrapper at {shim}{hint}"


def _chmodx(p: Path) -> None:
    st = p.stat()
    p.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


__all__ = [
    "service_name", "start_proxy", "install_startup", "verify_startup",
    "uninstall_runtime", "make_posix_shim", "make_windows_shim",
]
