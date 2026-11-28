"""Cross-platform core for the simplified Atlas installer.

Pure-stdlib. Handles OS detection, venv management, dependency installation,
OpenRouter key storage, proxy startup (detached), and health verification.

Setup flow (one command):
    1. Bootstrap   — detect OS, check Python >= 3.10, create venv, install deps
    2. Collect keys — prompt for OpenRouter API key(s), save to key file
    3. Start proxy — launch proxy.main detached, verify /health + keys loaded
    4. Exit        — report success, tell user how to stop

No harness configuration. No auto-start registration. No CLI shims.
Cross-platform: Linux, macOS, Windows.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
KEY_FILE_NAME = "data/openrouter_data/openroute_keys.txt"
RUNTIME_PROVIDER_FILE = "data/proxy_data/runtime_provider.json"
DEFAULT_PORT = 8000  # proxy.config.LISTEN_PORT default (this fork)
HEALTH_PATH = "/health"

SERVICE_NAME = "atlas-proxy.service"
"""systemd unit / launchd job / scheduled-task name. Shared across backends."""


# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------
def detect_os() -> str:
    """Return 'linux', 'macos', 'windows', or the raw platform name."""
    sysname = platform.system().lower()
    if sysname == "linux":
        return "linux"
    if sysname == "darwin":
        return "macos"
    if sysname in ("windows", "win32"):
        return "windows"
    return sysname


def is_windows() -> bool:
    return detect_os() == "windows"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def repo_root() -> Path:
    """Repository root = parent of the setup/ package dir."""
    return Path(__file__).resolve().parent.parent


def venv_dir(repo: Path) -> Path:
    return repo / ".venv"


def venv_python(repo: Path) -> Path:
    """Path to the venv's python executable (OS-aware)."""
    base = venv_dir(repo)
    if is_windows():
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python"


def venv_python_exists(repo: Path) -> bool:
    return venv_python(repo).is_file()


def key_file(repo: Path) -> Path:
    return repo / KEY_FILE_NAME


def runtime_provider_file(repo: Path) -> Path:
    return repo / RUNTIME_PROVIDER_FILE


def log_file(repo: Path) -> Path:
    return repo / "data" / "run.log"


def pid_file(repo: Path) -> Path:
    return repo / "data" / "run.pid"


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def ensure_data_dirs(repo: Path) -> None:
    """Create data subdirectories used by the proxy and installer."""
    (repo / "data" / "openrouter_data").mkdir(parents=True, exist_ok=True)
    (repo / "data" / "proxy_data").mkdir(parents=True, exist_ok=True)


def create_venv(repo: Path, quiet: bool = True) -> bool:
    """Create repo/.venv (idempotent). Returns True on success."""
    if venv_python_exists(repo):
        return True
    try:
        r = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir(repo))],
            capture_output=quiet, text=True, timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"venv creation failed: {e}", file=sys.stderr)
        return False
    return venv_python_exists(repo)


def install_requirements(repo: Path, venv_py: Path, quiet: bool = True) -> bool:
    """pip install -r requirements.txt into the venv (idempotent)."""
    req = repo / "requirements.txt"
    if not req.exists():
        print(f"[!] requirements.txt not found at {req}", file=sys.stderr)
        return False
    log = repo / ".installer.log"
    base = ["-q"] if quiet else ["-v"]
    try:
        with open(log, "ab") as lf:
            r = subprocess.run(
                [str(venv_py), "-m", "pip", "install", *base, "--no-input",
                 "-r", str(req)],
                stdout=lf, stderr=subprocess.STDOUT, timeout=600,
            )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"pip install failed: {e} (log: {log})", file=sys.stderr)
        return False
    if r.returncode != 0:
        print(f"pip install failed (rc={r.returncode}); see {log}", file=sys.stderr)
        return False
    return True


def deps_importable(repo: Path, venv_py: Path) -> bool:
    """True if the venv can import the proxy runtime deps."""
    try:
        r = subprocess.run(
            [str(venv_py), "-c", "import fastapi, uvicorn, httpx, orjson"],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def bootstrap(repo: Path) -> Path:
    """Full bootstrap: data dirs → venv → deps. Returns the venv python path."""
    ensure_data_dirs(repo)
    if not venv_python_exists(repo):
        print("→ creating virtualenv...", flush=True)
        if not create_venv(repo):
            raise RuntimeError("failed to create virtualenv")
    if not deps_importable(repo, venv_python(repo)):
        print("→ installing dependencies...", flush=True)
        if not install_requirements(repo, venv_python(repo)):
            raise RuntimeError("failed to install requirements")
    return venv_python(repo)


# ---------------------------------------------------------------------------
# OpenRouter keys
# ---------------------------------------------------------------------------
def read_keys(path: Path) -> list[str]:
    """Read existing keys from the key file (skips blanks and comments)."""
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]


def append_keys(path: Path, new_keys: list[str]) -> int:
    """Append de-duplicated keys to the key file. Returns count newly added.

    The file holds live API keys, so it is created 0600 and never left at the
    process umask default.
    """
    existing = set(read_keys(path))
    to_add = [k for k in new_keys if k not in existing]
    if not to_add:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = "" if (path.exists() and path.read_text().endswith("\n")) else "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(suffix + "\n".join(to_add) + "\n")
    finally:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return len(to_add)


KEY_PREFIXES = ("sk-", "sk-ant-", "sk-proj-", "sk-openai-", "nvapi-")
"""Accepted key prefixes, mirroring ``proxy.keypool._KEY_PREFIXES``.

Duplicated rather than imported: ``setup.core`` is stdlib-only and must run
before third-party deps (and therefore ``proxy.*``) are importable.  Keep the
two in sync — ``tests/test_windows_shim.py`` guards the ``sk-`` cases.
"""


def key_prefix_ok(key: str) -> bool:
    """Sanity check: does the key look like one the proxy can load?

    Mirrors ``proxy.keypool._KEY_PREFIXES`` — the loader that actually decides
    whether a key is usable.  It accepts any ``sk-`` key (OpenRouter's
    ``sk-or-``, plus ``sk-ant-``/``sk-proj-``/``sk-openai-``) and NVIDIA's
    ``nvapi-``.  Requiring ``sk-or-`` here warned on keys the proxy would
    happily load, so the check was a false negative rather than a safeguard.
    """
    return key.startswith(KEY_PREFIXES)


# ---------------------------------------------------------------------------
# OS-specific setup module
# ---------------------------------------------------------------------------
def _os_module():
    """Return the OS-specific setup module (linux/macos/windows_setup).

    Re-exported here because the installers (install.ps1 -Check/-Uninstall,
    install.sh --check/--uninstall) call ``core._os_module()`` from an inline
    script.  It originally lived only in ``setup.wizard``, so every one of
    those paths died with AttributeError.  ``setup.wizard`` keeps its own
    wrapper and delegates here, so there is a single source of truth.
    """
    import importlib

    return importlib.import_module(f".{_detect_os_name()}_setup", package="setup")


def _detect_os_name() -> str:
    """Map the host to a setup-module suffix (linux/macos/windows)."""
    sysname = platform.system().lower()
    if sysname == "darwin":
        return "macos"
    if sysname in ("windows", "win32"):
        return "windows"
    if sysname == "linux":
        return "linux"
    # Unknown POSIX-ish host: linux_setup is the closest safe default.
    return "linux"


def start_proxy(repo: Path) -> tuple[bool, str]:
    """Start the proxy via the OS supervisor (see setup._supervisor)."""
    from . import _supervisor

    return _supervisor.start_proxy(repo)


def verify_startup(repo: Path) -> tuple[bool, str]:
    """Report the active runtime's status (see setup._supervisor)."""
    from . import _supervisor

    return _supervisor.verify_startup(repo)


def uninstall_runtime(repo: Path) -> tuple[bool, str]:
    """Stop the proxy and remove its runtime config (see setup._supervisor)."""
    from . import _supervisor

    return _supervisor.uninstall_runtime(repo)


# ---------------------------------------------------------------------------
# Proxy config
# ---------------------------------------------------------------------------
def proxy_port(repo: Path) -> int:
    """Resolve the port the proxy will bind to.

    Priority: LISTEN_PORT env -> proxy.config.LISTEN_PORT -> 8000.
    """
    envp = os.environ.get("LISTEN_PORT")
    if envp and envp.isdigit():
        return int(envp)
    sys.path.insert(0, str(repo))
    try:
        from proxy.config import LISTEN_PORT  # type: ignore
        return int(LISTEN_PORT)
    except Exception:
        return DEFAULT_PORT


def health_url(repo: Path) -> str:
    return f"http://127.0.0.1:{proxy_port(repo)}{HEALTH_PATH}"


def write_runtime_provider(repo: Path, provider: str = "openrouter",
                           model: str = "") -> None:
    """Write data/proxy_data/runtime_provider.json so the proxy uses OpenRouter."""
    rp = runtime_provider_file(repo)
    rp.parent.mkdir(parents=True, exist_ok=True)
    payload = {"provider": provider, "model": model, "timestamp": int(time.time())}
    rp.write_text(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
def health_check(repo: Path, timeout: float = 8.0) -> tuple[bool, str]:
    """Bounded poll of the proxy /health endpoint."""
    import urllib.request
    import urllib.error
    url = health_url(repo)
    deadline = time.monotonic() + timeout
    last = "no response"
    # Loopback must never go through a system/VPN proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=1.0) as r:
                if 200 <= r.status < 300:
                    return True, f"healthy ({r.status}) @ {url}"
                last = f"status {r.status}"
        except urllib.error.HTTPError as e:
            last = f"http {e.code}"
        except (urllib.error.URLError, ConnectionError, OSError):
            last = "unreachable"
        time.sleep(0.3)
    return False, f"unhealthy: {last} ({url})"


def is_port_in_use(host: str, port: int, timeout: float = 1.0) -> bool:
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Start proxy (detached, background)
# ---------------------------------------------------------------------------
def start_proxy_detached(repo: Path, venv_py: Path) -> tuple[bool, str, int | None]:
    """Launch proxy.main as a detached background process.

    Returns (ok, message, pid). If pid is None, an already-healthy proxy
    was detected on the port (nothing to start).
    """
    port = proxy_port(repo)

    # Is something already listening on the port?
    if is_port_in_use("127.0.0.1", port):
        ok, msg = health_check(repo, timeout=2.0)
        if ok:
            return True, f"proxy already running on port {port}", None
        return False, (f"port {port} is in use but proxy is not healthy: {msg}. "
                       "Stop whatever is on that port and re-run.")

    log = log_file(repo)
    log.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log, "ab")

    try:
        if is_windows():
            proc = subprocess.Popen(
                [str(venv_py), "-m", "proxy.main"],
                stdout=log_fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, cwd=str(repo),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            proc = subprocess.Popen(
                [str(venv_py), "-m", "proxy.main"],
                stdout=log_fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, cwd=str(repo),
                start_new_session=True,
            )
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        return False, f"could not start proxy: {e}", None

    # Keep the log file handle open for the lifetime of the process so
    # output isn't lost if the file is rotated externally.
    proc._log_fh = log_fh  # type: ignore[attr-defined]

    # Save pid for the user's reference
    try:
        pid_file(repo).parent.mkdir(parents=True, exist_ok=True)
        pid_file(repo).write_text(str(proc.pid))
    except OSError:
        pass

    return True, f"proxy starting (pid={proc.pid}, log={log})", proc.pid


__all__ = [
    "detect_os", "is_windows",
    "repo_root", "venv_python", "venv_python_exists",
    "key_file", "runtime_provider_file", "log_file", "pid_file",
    "ensure_data_dirs", "create_venv", "install_requirements",
    "deps_importable", "bootstrap",
    "read_keys", "append_keys", "key_prefix_ok", "KEY_PREFIXES",
    "proxy_port", "health_url", "write_runtime_provider",
    "health_check", "is_port_in_use", "start_proxy_detached",
    "_os_module", "verify_startup", "uninstall_runtime", "start_proxy",
]
