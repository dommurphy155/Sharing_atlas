"""Cross-platform core for the Atlas installer.

Pure-stdlib. Importable even before the venv/deps exist so the bootstrap path
(python3 -> venv -> deps -> wizard) can run the copy + venv creation steps.
"""
from __future__ import annotations

import json  # noqa: F401  (used by runtime_provider + key helpers)
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
DUMMY_KEY = "sk-or-dummy"
"""Placeholder client key given to the harness. The proxy does NOT validate the
client-supplied key against any list — it pulls a real upstream key from its
own pool and sends that upstream. So any non-empty bearer works as the client
credential. We verify with a real /v1/chat/completions round-trip."""

SERVICE_NAME = "atlas-proxy.service"
"""systemd unit / launchd job / scheduled-task name. Shared across backends."""

# data/<provider>_data/<keys> — kept in sync with proxy/config.py defaults
_PROVIDER_KEY_FILES = {
    "openrouter":  "openrouter_data/openroute_keys.txt",
    "nvidia":      "nvda_data/keys.txt",
}
_DEAD_KEY_FILES = {
    "openrouter":  None,
    "nvidia":      "nvda_data/dead_keys.txt",
}


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------
def detect_os() -> str:
    sysname = platform.system().lower()
    if sysname == "linux":
        return "linux"
    if sysname == "darwin":
        return "macos"
    if sysname in ("windows", "win32"):
        return "windows"
    return sysname


def detect_arch() -> str:
    m = platform.machine().lower()
    # Normalise the common aliases so install scripts can branch on a few.
    if m in ("x86_64", "amd64", "x64"):
        return "x86_64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    return m or "unknown"


def is_windows() -> bool:
    return detect_os() == "windows"


def is_posix() -> bool:
    return detect_os() in ("linux", "macos")


def in_venv() -> bool:
    """True if this interpreter is running inside an Atlas venv."""
    return ".venv" in Path(sys.executable).resolve().parts


# ---------------------------------------------------------------------------
# Layout / paths
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


def venv_pip(repo: Path) -> list[str]:
    return [str(venv_python(repo)), "-m", "pip"]


def venv_python_exists(repo: Path) -> bool:
    return venv_python(repo).is_file()


def data_dir(repo: Path) -> Path:
    return repo / "data"


def key_file_for(repo: Path, provider: str) -> Path:
    rel = _PROVIDER_KEY_FILES.get(provider)
    if not rel:
        raise ValueError(f"unknown provider: {provider}")
    return repo / "data" / rel


def ensure_data_dirs(repo: Path) -> None:
    """Create all provider/data subdirectories used by the proxy."""
    (data_dir(repo) / "openrouter_data").mkdir(parents=True, exist_ok=True)
    (data_dir(repo) / "nvda_data").mkdir(parents=True, exist_ok=True)
    (data_dir(repo) / "proxy_data").mkdir(parents=True, exist_ok=True)
    (repo / "proxy" / "logs").mkdir(parents=True, exist_ok=True)


def write_runtime_provider(repo: Path, provider: str, model: str = "") -> None:
    rp = data_dir(repo) / "proxy_data" / "runtime_provider.json"
    rp.parent.mkdir(parents=True, exist_ok=True)
    payload = {"provider": provider, "model": model, "timestamp": int(time.time())}
    rp.write_text(json.dumps(payload, indent=2))


def ensure_dotenv(repo: Path) -> None:
    """Materialise repo/.env from proxy/.env.example so the systemd
    EnvironmentFile= directive has something to read. Never overwrites an
    existing file (the user's keys live outside .env, in key files)."""
    env = repo / ".env"
    example = repo / "proxy" / ".env.example"
    if env.exists():
        return
    if example.exists():
        env.write_text(example.read_text())


# ---------------------------------------------------------------------------
# Port + health (single source of truth: proxy.config.LISTEN_PORT)
# ---------------------------------------------------------------------------
def proxy_port(repo: Path) -> int:
    """Resolve the port the proxy will bind to.

    Priority: LISTEN_PORT env -> proxy.config.LISTEN_PORT -> 8000.
    Import proxy.config lazily (it is stdlib-only and safe to import without
    the proxy runtime deps, but we still guard for the pre-deps bootstrap).
    """
    envp = os.environ.get("LISTEN_PORT")
    if envp and envp.isdigit():
        return int(envp)
    sys.path.insert(0, str(repo))
    try:
        from proxy.config import LISTEN_PORT  # type: ignore
        return int(LISTEN_PORT)
    except Exception:
        return 8000


def proxy_host(repo: Path) -> str:
    sys.path.insert(0, str(repo))
    try:
        from proxy.config import LISTEN_HOST  # type: ignore
        host = LISTEN_HOST
        return "127.0.0.1" if host in ("0.0.0.0", "") else host
    except Exception:
        return "127.0.0.1"


def proxy_health_url(repo: Path) -> str:
    return f"http://{proxy_host(repo)}:{proxy_port(repo)}/health"


def health_check(repo: Path, timeout: float = 8.0) -> tuple[bool, str]:
    """Bounded poll of the proxy /health endpoint."""
    import urllib.request
    import urllib.error
    import time
    url = proxy_health_url(repo)
    deadline = time.monotonic() + timeout
    last = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as r:
                if 200 <= r.status < 300:
                    return True, f"healthy ({r.status}) @ {url}"
                last = f"status {r.status}"
        except urllib.error.HTTPError as e:
            last = f"http {e.code}"
        except (urllib.error.URLError, ConnectionError, OSError):
            last = "unreachable"
        time.sleep(0.3)
    return False, f"unhealthy: {last} ({url})"


def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Subprocess helpers (cross-platform)
# ---------------------------------------------------------------------------
def run(cmd, timeout: float = 30.0, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a command list. On Windows, .cmd/.ps1 launchers need shell=True."""
    shell = False
    if is_windows() and isinstance(cmd, (list, tuple)) and isinstance(cmd[0], str):
        first = cmd[0]
        # python -m ... style still fine as a list; only shell builtins / .cmd
        # names need the shell.
        if first.endswith(".cmd") or first.endswith(".ps1") or first in ("powershell", "pwsh"):
            shell = True
            cmd = " ".join(_win_quote(c) for c in cmd[1:]) if first in ("powershell", "pwsh") else " ".join(cmd)
            return subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout, shell=True)
    return subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout, shell=shell)


def _win_quote(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"'


def run_powershell(script: str, timeout: float = 120.0, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a PowerShell snippet. Used for Windows-only operations."""
    ps = os.environ.get("ATLAS_PS", None) or shutil.which("pwsh") or shutil.which("powershell")
    if not ps:
        raise FileNotFoundError("neither pwsh nor powershell found on PATH")
    return subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "ByPass", "-Command", script],
        capture_output=capture, text=True, timeout=timeout,
    )


# ---------------------------------------------------------------------------
# venv + deps
# ---------------------------------------------------------------------------
def create_venv(repo: Path, quiet: bool = True) -> bool:
    """Create repo/.venv (idempotent). Returns True on success."""
    venv = venv_dir(repo)
    if venv_python_exists(repo):
        return True
    import_cmd = [sys.executable, "-m", "venv", str(venv)]
    try:
        r = subprocess.run(import_cmd, capture_output=quiet, text=True, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"venv creation failed: {e}", file=sys.stderr)
        return False
    return venv_python_exists(repo)


def _tail_log(log: Path, n: int = 25) -> None:
    """Print the last n lines of an installer log (best-effort)."""
    try:
        lines = log.read_text(errors="replace").splitlines()
        for ln in lines[-n:]:
            print("  " + ln, file=sys.stderr)
    except Exception:
        pass


def install_requirements(repo: Path, quiet: bool = True) -> bool:
    """pip install -r requirements.txt into the repo venv (idempotent).

    Output is streamed to <repo>/.installer.log instead of being captured in
    memory: pip's verbose mode emits a large stream that can fill the
    subprocess pipe buffer and deadlock against the timeout, falsely reporting
    "failed to install requirements" for a perfectly healthy install.
    """
    req = repo / "requirements.txt"
    if not req.exists():
        return False
    log = repo / ".installer.log"
    base = ["-q"] if quiet else ["-v"]
    try:
        with open(log, "ab") as lf:
            r = subprocess.run(venv_pip(repo) + ["install", *base, "--no-input",
                                                 "-r", str(req)],
                               stdout=lf, stderr=subprocess.STDOUT, timeout=600)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"pip install failed: {e} (log: {log})", file=sys.stderr)
        _tail_log(log)
        return False
    if r.returncode != 0:
        print(f"pip install failed (rc={r.returncode}); see {log}", file=sys.stderr)
        _tail_log(log)
        return False
    return True


def ensure_environment(repo: Path, quiet: bool = True) -> Path:
    """Make sure the repo has a venv with all deps. Returns the venv python.

    Safe to rerun. Used by both the first-run installer and `atlas install`.
    """
    ensure_data_dirs(repo)
    if not venv_python_exists(repo):
        if not create_venv(repo, quiet=quiet):
            raise RuntimeError("failed to create virtualenv")
    if not _deps_importable(repo):
        if not install_requirements(repo, quiet=quiet):
            raise RuntimeError("failed to install requirements")
    return venv_python(repo)


def _deps_importable(repo: Path) -> bool:
    """True if the venv can import the proxy + CLI runtime deps."""
    py = venv_python(repo)
    if not py.is_file():
        return False
    try:
        r = subprocess.run(
            [str(py), "-c", "import fastapi, uvicorn, httpx, orjson, rich"],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


# ---------------------------------------------------------------------------
# Source copy (self-contained install to the user's chosen dir)
# ---------------------------------------------------------------------------
# Exclude secrets, caches, the venv, and VCS metadata so a copied install
# never leaks keys or drags in compiled/interpreted cruft.
_COPY_IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "__pycache__", "*.pyc", "*.pyo", "*.egg-info",
    "openroute_keys.txt", "nvapi_keys.txt", "keys.txt",
    "dead_keys.txt",
)


def copy_source_to(src: Path, dst: Path) -> Path:
    """Copy the repo to *dst* (self-contained install), leaving *src* intact.

    Idempotent: if ``dst`` already contains a proxy package + venv it is left
    as-is (an existing install). Caller decides whether to re-copy.
    """
    dst = dst.resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_COPY_IGNORE)
    return dst


def is_atlas_repo(path: Path) -> bool:
    return (path / "proxy" / "main.py").is_file()
