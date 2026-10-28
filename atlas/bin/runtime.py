"""Atlas proxy runtime abstraction.

Decides — at install time and at every command invocation — which mechanism
Atlas should use to run, supervise, and surface logs for the proxy process.

The runtime is selected at install time and persisted to
`<repo>/data/runtime.json` so subsequent commands (`start`, `stop`, `status`,
`logs`, `doctor`) know what to drive without re-detecting the world every
time. Re-running `atlas install` re-detects and re-selects.

Selection priority (highest first):
  1. systemd system  — Linux + root OR sudo works + systemctl + systemd PID 1
  2. systemd --user  — Linux + XDG_RUNTIME_DIR + systemctl --user + loginctl
                        session for current user
  3. tmux-equivalent — any of: tmux, psmux, tmuxw, lumux, qscn, wmux
  4. nohup          — POSIX nohup (always available on unix) — last
                       mechanical fallback. Foreground `atlas start` and
                       detached `nohup` are not equivalent: nohup survives
                       terminal close; `atlas start` foreground blocks.
  5. manual         — Windows without a tmux-equivalent. User runs the
                       proxy as a foreground process from their shell.

The "systemd system" mode is always preferred when actually usable. The
fallbacks only kick in when root / sudo / systemd genuinely are unavailable.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Protocol


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------

OSFamily = Literal["linux", "macos", "windows", "other"]
RuntimeMode = Literal["systemd", "systemd-user", "tmux", "nohup", "manual",
                      "launchd", "scheduled-task"]

# Tmux-equivalent binaries (tmux itself + native Windows clones).
TMUX_BINARIES = ("tmux", "psmux", "tmuxw", "lumux", "qscn", "wmux")


@dataclass
class RuntimeEnv:
    """Snapshot of the host environment relevant to runtime selection.

    Frozen after detection — all fields are read-only facts, not preferences.
    """
    os: OSFamily
    is_root: bool
    sudo_works: bool
    has_systemctl: bool
    systemd_pid1: bool
    has_user_systemd: bool
    has_loginctl: bool
    tmux_bin: str | None  # first tmux-equivalent on PATH
    has_nohup: bool
    has_launchctl: bool = False  # macOS launchd availability
    has_tasklist: bool = False  # Windows schtasks availability

    # Convenience derivations
    @property
    def systemd_system_usable(self) -> bool:
        return (
            self.os == "linux"
            and self.has_systemctl
            and self.systemd_pid1
            and (self.is_root or self.sudo_works)
        )

    @property
    def systemd_user_usable(self) -> bool:
        # Root can also use systemd --user if a user bus exists
        # (e.g. via logind). Non-root needs XDG_RUNTIME_DIR set.
        if self.os != "linux" or not self.has_systemctl:
            return False
        if not self.has_user_systemd:
            return False
        # Verify the user bus is actually reachable by checking
        # if the runtime directory exists and systemctl --user
        # can communicate with it.
        if self.is_root:
            # Root: check for a user bus (logind session).
            # XDG_RUNTIME_DIR may or may not be set for root.
            xdg = os.environ.get("XDG_RUNTIME_DIR", "")
            if not xdg:
                try:
                    xdg = f"/run/user/{os.getuid()}"
                except AttributeError:
                    return False
            return Path(xdg).is_dir() and self.has_loginctl
        # Non-root: XDG_RUNTIME_DIR must be set and valid.
        xdg = os.environ.get("XDG_RUNTIME_DIR", "")
        if not xdg:
            try:
                xdg = f"/run/user/{os.getuid()}"
            except AttributeError:
                return False
        return bool(xdg and Path(xdg).is_dir())

    @property
    def tmux_usable(self) -> bool:
        return self.tmux_bin is not None

    @property
    def nohup_usable(self) -> bool:
        return self.os in ("linux", "macos", "other") and self.has_nohup

    @property
    def launchd_usable(self) -> bool:
        # macOS native supervisor — auto-starts at login, survives reboot.
        return self.os == "macos" and self.has_launchctl

    @property
    def scheduled_task_usable(self) -> bool:
        # Windows Task Scheduler — auto-starts at log-on, survives reboot.
        return self.os == "windows" and self.has_tasklist


def detect_os() -> OSFamily:
    sysname = platform.system().lower()
    if sysname == "linux":
        return "linux"
    if sysname == "darwin":
        return "macos"
    if sysname in ("windows", "win32"):
        return "windows"
    return "other"


def _run_quiet(cmd: list[str], timeout: float = 2.0) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _find_tmux() -> str | None:
    for b in TMUX_BINARIES:
        p = shutil.which(b)
        if p:
            return p
    return None


def detect_env() -> RuntimeEnv:
    """Snapshot of the current host — pure, no side effects, no I/O."""
    os_name = detect_os()
    is_root = False
    try:
        is_root = (os.geteuid() == 0)  # POSIX
    except AttributeError:
        # Windows — no geteuid. Check for Administrator via net session.
        r = _run_quiet(["net", "session"], timeout=2.0)
        is_root = bool(r and r.returncode == 0)

    # sudo -n true: succeeds (rc=0) iff sudo exists AND is allowed without
    # a password. If it would prompt for a password, we treat sudo as
    # non-functional for our purposes — better to fall back than hang.
    # Having sudo installed is enough to make systemd a viable runtime
    # candidate.  Privileged operations intentionally invoke normal sudo so
    # the user can authenticate interactively when required.
    sudo_works = bool(shutil.which("sudo"))

    has_systemctl = shutil.which("systemctl") is not None

    # systemd as PID 1 — the canonical "systemd is the system manager" check.
    systemd_pid1 = os_name == "linux" and Path("/proc/1/comm").exists() \
        and Path("/proc/1/comm").read_text().strip() == "systemd"

    # Per-user systemd: needs XDG_RUNTIME_DIR=/run/user/<uid> + systemctl --user
    # reachable. loginctl is a useful proxy for "is there a login session".
    has_user_systemd = False
    has_loginctl = shutil.which("loginctl") is not None
    if os_name == "linux" and has_systemctl and not is_root:
        xdg = os.environ.get("XDG_RUNTIME_DIR", "")
        if not xdg:
            try:
                xdg = f"/run/user/{os.getuid()}"
            except AttributeError:
                xdg = ""
        if xdg and Path(xdg).is_dir():
            r = _run_quiet(["systemctl", "--user", "is-active", "atlas-proxy.service"])
            # Don't require the service to exist; just require the call to not
            # fail with "Failed to connect to bus".
            if r and "Failed to connect" not in (r.stderr or ""):
                has_user_systemd = True

    tmux_bin = _find_tmux()
    # macOS launchd (native auto-start / supervisor).
    has_launchctl = os_name == "macos" and shutil.which("launchctl") is not None
    # Windows Task Scheduler (native auto-start / supervisor).
    has_tasklist = os_name == "windows" and shutil.which("schtasks") is not None
    # Windows: also check PATHEXT-less exe names; shutil.which handles that.

    # nohup is a shell builtin on some shells but also a binary on most
    # unix systems. Test for the binary form. Windows has no equivalent —
    # we'd never invoke nohup there.
    has_nohup = os_name in ("linux", "macos", "other") and shutil.which("nohup") is not None

    return RuntimeEnv(
        os=os_name,
        is_root=is_root,
        sudo_works=sudo_works,
        has_systemctl=has_systemctl,
        systemd_pid1=systemd_pid1,
        has_user_systemd=has_user_systemd,
        has_loginctl=has_loginctl,
        has_launchctl=has_launchctl,
        has_tasklist=has_tasklist,
        tmux_bin=tmux_bin,
        has_nohup=has_nohup,
    )


def choose_mode(env: RuntimeEnv) -> RuntimeMode:
    """Pick the best runtime mode for the given environment.

    Order of preference (highest first):
      1. systemd         — full system service, root or sudo
      2. systemd-user    — user-scoped systemd service
      3. launchd         — macOS native (auto-start at login, survives reboot)
      4. tmux            — detached user tmux session
      5. nohup           — POSIX nohup, last mechanical fallback
      6. scheduled-task  — Windows Task Scheduler (auto-start at log-on)
      7. manual          — no supervisor available; user runs it themselves
    """
    if env.systemd_system_usable:
        return "systemd"
    if env.systemd_user_usable:
        return "systemd-user"
    if env.launchd_usable:
        return "launchd"
    if env.tmux_usable:
        return "tmux"
    if env.nohup_usable:
        return "nohup"
    if env.scheduled_task_usable:
        return "scheduled-task"
    return "manual"


# ---------------------------------------------------------------------------
# Runtime-mode implementations
# ---------------------------------------------------------------------------
#
# A Runtime is a strategy for managing the proxy process: start it, stop it,
# check if it's running, and surface its logs. Each impl is small and
# self-contained. The CLI commands dispatch to the right one via get_runtime().


@dataclass
class RuntimeInfo:
    """One-line description of the active runtime — used by `atlas status`."""
    mode: RuntimeMode
    label: str        # "systemd" / "systemd-user" / "tmux" / "nohup" / "manual"
    session: str      # systemd unit / tmux session name / "atlas2-proxy"
    pid: int | None
    extra: dict = field(default_factory=dict)


class Runtime(Protocol):
    """Common interface for all runtime implementations."""
    info: RuntimeInfo

    def start(self) -> tuple[bool, str]: ...
    def stop(self) -> tuple[bool, str]: ...
    def status(self) -> tuple[bool, str]: ...
    def logs(self, follow: bool = True) -> int: ...
    def env_summary(self) -> dict: ...
    def cleanup(self) -> None: ...


# ---- systemd (system) -----------------------------------------------------

class SystemdRuntime:
    def __init__(self, env: RuntimeEnv, repo_root: Path, service_name: str,
                 venv_py: Path) -> None:
        self.env = env
        self.repo_root = repo_root
        self.service_name = service_name
        self.venv_py = venv_py
        self.info = RuntimeInfo(
            mode="systemd",
            label="systemd",
            session=service_name,
            pid=None,
        )

    def _sudo_prefix(self) -> list[str]:
        # Do not use --non-interactive: a normal sudo user may need to enter
        # their password during installation or service management.
        return [] if self.env.is_root else ["sudo"]

    def _run(self, *args: str) -> subprocess.CompletedProcess | None:
        cmd = self._sudo_prefix() + ["systemctl", *args]
        return _run_quiet(cmd, timeout=10.0)

    def install(self) -> tuple[bool, str]:
        unit = f"""[Unit]
Description=Atlas Proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={self.repo_root}
ExecStart={self.venv_py} -m proxy.main
Restart=on-failure
RestartSec=5
EnvironmentFile=-{self.repo_root / ".env"}

[Install]
WantedBy=multi-user.target
"""
        unit_path = Path("/etc/systemd/system") / self.service_name
        tmp = self.repo_root / "data" / f".{self.service_name}.tmp"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(unit)

        try:
            r = self._run("stop", self.service_name)
            install = self._sudo_prefix() + [
                "install", "-m", "644", str(tmp), str(unit_path)
            ]
            ir = _run_quiet(install, timeout=10.0)
            if ir is None or ir.returncode != 0:
                return False, ir.stderr.strip() if ir else "failed to install systemd unit"

            rr = self._run("daemon-reload")
            if rr is None or rr.returncode != 0:
                return False, rr.stderr.strip() if rr else "systemctl daemon-reload failed"

            er = self._run("enable", self.service_name)
            if er is None or er.returncode != 0:
                return False, er.stderr.strip() if er else "systemctl enable failed"

            sr = self._run("start", self.service_name)
            if sr is None or sr.returncode != 0:
                return False, sr.stderr.strip() if sr else "systemctl start failed"

            return True, f"installed and started {self.service_name}"
        finally:
            tmp.unlink(missing_ok=True)

    def cleanup(self) -> None:
        """Best-effort: stop + disable + remove the unit. Never raises."""
        try:
            self._run("disable", "--now", self.service_name)
        except Exception:
            pass
        unit_path = Path("/etc/systemd/system") / self.service_name
        if unit_path.exists():
            install = self._sudo_prefix() + ["rm", "-f", str(unit_path)]
            _run_quiet(install, timeout=5.0)
        try:
            self._run("daemon-reload")
        except Exception:
            pass

    def start(self) -> tuple[bool, str]:
        r = self._run("start", self.service_name)
        if r is None or r.returncode != 0:
            return False, (r.stderr.strip() if r else "systemctl not available")
        return True, f"started {self.service_name}"

    def stop(self) -> tuple[bool, str]:
        r = self._run("stop", self.service_name)
        if r is None or r.returncode != 0:
            return False, (r.stderr.strip() if r else "systemctl not available")
        return True, f"stopped {self.service_name}"

    def status(self) -> tuple[bool, str]:
        r = self._run("is-active", self.service_name)
        if r is None:
            return False, "systemctl unavailable"
        active = r.stdout.strip() == "active"
        return active, r.stdout.strip()

    def logs(self, follow: bool = True) -> int:
        cmd = ["journalctl", "-u", self.service_name, "--no-pager"]
        if follow:
            cmd.append("-f")
        # journalctl usually needs sudo for system units
        cmd = self._sudo_prefix() + cmd
        try:
            return subprocess.call(cmd)
        except FileNotFoundError:
            return 1

    def env_summary(self) -> dict:
        return {"mode": "systemd", "service_name": self.service_name}


# ---- systemd --user -------------------------------------------------------

class SystemdUserRuntime:
    def __init__(self, env: RuntimeEnv, repo_root: Path, service_name: str,
                 venv_py: Path) -> None:
        self.env = env
        self.repo_root = repo_root
        self.service_name = service_name
        self.venv_py = venv_py
        self.info = RuntimeInfo(
            mode="systemd-user",
            label="systemd --user",
            session=service_name,
            pid=None,
        )

    def _run(self, *args: str) -> subprocess.CompletedProcess | None:
        cmd = ["systemctl", "--user", *args]
        return _run_quiet(cmd, timeout=10.0)

    def install(self) -> tuple[bool, str]:
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        unit_path = unit_dir / self.service_name

        unit = f"""[Unit]
Description=Atlas Proxy
After=network-online.target

[Service]
Type=simple
WorkingDirectory={self.repo_root}
ExecStart={self.venv_py} -m proxy.main
Restart=on-failure
RestartSec=5
EnvironmentFile=-{self.repo_root / ".env"}

[Install]
WantedBy=default.target
"""

        unit_dir.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(unit)

        r = self._run("daemon-reload")
        if r is None or r.returncode != 0:
            return False, r.stderr.strip() if r else "systemctl --user daemon-reload failed"

        r = self._run("enable", self.service_name)
        if r is None or r.returncode != 0:
            return False, r.stderr.strip() if r else "systemctl --user enable failed"

        r = self._run("start", self.service_name)
        if r is None or r.returncode != 0:
            return False, r.stderr.strip() if r else "systemctl --user start failed"

        return True, f"installed and started {self.service_name}"

    def cleanup(self) -> None:
        """Best-effort: stop + disable + remove the user unit. Never raises."""
        try:
            self._run("disable", "--now", self.service_name)
        except Exception:
            pass
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        unit_path = unit_dir / self.service_name
        if unit_path.exists():
            try:
                unit_path.unlink()
            except OSError:
                pass
        try:
            self._run("daemon-reload")
        except Exception:
            pass

    def start(self) -> tuple[bool, str]:
        r = self._run("start", self.service_name)
        if r is None or r.returncode != 0:
            return False, (r.stderr.strip() if r else "systemctl --user unavailable")
        return True, f"started {self.service_name}"

    def stop(self) -> tuple[bool, str]:
        r = self._run("stop", self.service_name)
        if r is None or r.returncode != 0:
            return False, (r.stderr.strip() if r else "systemctl --user unavailable")
        return True, f"stopped {self.service_name}"

    def status(self) -> tuple[bool, str]:
        r = self._run("is-active", self.service_name)
        if r is None:
            return False, "systemctl --user unavailable"
        active = r.stdout.strip() == "active"
        return active, r.stdout.strip()

    def logs(self, follow: bool = True) -> int:
        cmd = ["journalctl", "--user", "-u", self.service_name, "--no-pager"]
        if follow:
            cmd.append("-f")
        try:
            return subprocess.call(cmd)
        except FileNotFoundError:
            return 1

    def env_summary(self) -> dict:
        return {"mode": "systemd-user", "service_name": self.service_name}


# ---- tmux ----------------------------------------------------------------

TMUX_SESSION = "atlas"


class TmuxRuntime:
    def __init__(self, env: RuntimeEnv, repo_root: Path, tmux_bin: str,
                 venv_py: Path, log_file: Path) -> None:
        self.env = env
        self.repo_root = repo_root
        self.tmux_bin = tmux_bin
        self.venv_py = venv_py
        self.log_file = log_file
        self.session = TMUX_SESSION
        self.info = RuntimeInfo(
            mode="tmux",
            label=f"tmux ({Path(tmux_bin).name})",
            session=self.session,
            pid=None,
        )

    def _run(self, *args: str) -> subprocess.CompletedProcess | None:
        return _run_quiet([self.tmux_bin, *args], timeout=5.0)

    def start(self) -> tuple[bool, str]:
        if self.status()[0]:
            return True, f"already running in tmux session '{self.session}'"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        # -d: detach. -s: session name. The proxy is launched inside the
        # tmux session so its stdout/stderr go to the tmux scrollback; we
        # ALSO tee to a log file for `atlas logs` to tail.
        cmd = (
            f"exec {self.venv_py} -m proxy.main 2>&1 | "
            f"tee -a {shlex_quote(str(self.log_file))}"
        )
        r = self._run(
            "new-session", "-d", "-s", self.session, "-c", str(self.repo_root),
            "bash", "-lc", cmd,
        )
        if r is None or r.returncode != 0:
            return False, (r.stderr.strip() if r else f"{self.tmux_bin} failed")
        return True, f"started in tmux session '{self.session}'"

    def stop(self) -> tuple[bool, str]:
        if not self.status()[0]:
            return True, "not running"
        r = self._run("kill-session", "-t", self.session)
        if r is None or r.returncode != 0:
            return False, (r.stderr.strip() if r else f"{self.tmux_bin} kill failed")
        return True, f"killed tmux session '{self.session}'"

    def cleanup(self) -> None:
        """Best-effort: kill the tmux session. Never raises."""
        try:
            self._run("kill-session", "-t", self.session)
        except Exception:
            pass

    def status(self) -> tuple[bool, str]:
        r = self._run("has-session", "-t", self.session)
        if r is None:
            return False, f"{self.tmux_bin} unavailable"
        return r.returncode == 0, \
            (f"running in tmux '{self.session}'" if r.returncode == 0 else "stopped")

    def logs(self, follow: bool = True) -> int:
        # Tail the tee'd log file. tmux itself has capture-pane but a flat
        # file is more portable across tmux equivalents.
        if not self.log_file.exists():
            print(f"No log file yet: {self.log_file}", file=sys.stderr)
            return 1
        cmd = ["tail", "-n", "40"]
        if follow:
            cmd.append("-f")
        cmd.append(str(self.log_file))
        try:
            return subprocess.call(cmd)
        except FileNotFoundError:
            return 1

    def env_summary(self) -> dict:
        return {
            "mode": "tmux",
            "tmux_bin": self.tmux_bin,
            "session": self.session,
            "log_file": str(self.log_file),
        }


# ---- nohup (POSIX fallback) ----------------------------------------------

class NohupRuntime:
    """Last mechanical fallback: nohup + pidfile. Foreground mode for manual."""

    def __init__(self, env: RuntimeEnv, repo_root: Path, venv_py: Path,
                 pid_file: Path, log_file: Path) -> None:
        self.env = env
        self.repo_root = repo_root
        self.venv_py = venv_py
        self.pid_file = pid_file
        self.log_file = log_file
        self.info = RuntimeInfo(
            mode="nohup",
            label="nohup (user process)",
            session="atlas-proxy",
            pid=None,
        )

    def start(self) -> tuple[bool, str]:
        if self.status()[0]:
            return True, f"already running (pid={self._pid()})"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("ab") as fh:
            proc = subprocess.Popen(
                [str(self.venv_py), "-m", "proxy.main"],
                stdout=fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=str(self.repo_root),
                start_new_session=True,
            )
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(str(proc.pid))
        return True, f"started (pid={proc.pid}, log={self.log_file})"

    def stop(self) -> tuple[bool, str]:
        pid = self._pid()
        if pid is None:
            self.pid_file.unlink(missing_ok=True)
            return True, "not running"
        try:
            os.kill(pid, 15)  # SIGTERM
        except ProcessLookupError:
            self.pid_file.unlink(missing_ok=True)
            return True, "not running"
        # Wait briefly for graceful exit
        for _ in range(20):
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except ProcessLookupError:
                break
        else:
            try:
                os.kill(pid, 9)  # SIGKILL fallback
            except ProcessLookupError:
                pass
        self.pid_file.unlink(missing_ok=True)
        return True, f"stopped (pid={pid})"

    def status(self) -> tuple[bool, str]:
        pid = self._pid()
        if pid is None:
            return False, "not running"
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            self.pid_file.unlink(missing_ok=True)
            return False, "not running"
        return True, f"running (pid={pid})"

    def logs(self, follow: bool = True) -> int:
        if not self.log_file.exists():
            print(f"No log file yet: {self.log_file}", file=sys.stderr)
            return 1
        cmd = ["tail", "-n", "40"]
        if follow:
            cmd.append("-f")
        cmd.append(str(self.log_file))
        try:
            return subprocess.call(cmd)
        except FileNotFoundError:
            return 1

    def _pid(self) -> int | None:
        if not self.pid_file.exists():
            return None
        try:
            return int(self.pid_file.read_text().strip())
        except (ValueError, OSError):
            return None

    def cleanup(self) -> None:
        """Best-effort: stop + remove pidfile. Never raises."""
        try:
            self.stop()
        except Exception:
            pass
        self.pid_file.unlink(missing_ok=True)

    def env_summary(self) -> dict:
        return {
            "mode": "nohup",
            "pid_file": str(self.pid_file),
            "log_file": str(self.log_file),
        }


# ---- manual (Windows / last resort) --------------------------------------

class ManualRuntime:
    """No persistent supervisor available — user runs `atlas start` fg."""

    def __init__(self) -> None:
        self.info = RuntimeInfo(
            mode="manual",
            label="manual (foreground)",
            session="atlas-proxy",
            pid=None,
        )

    def start(self) -> tuple[bool, str]:
        return False, (
            "No persistent supervisor available on this system.\n"
            "Run the proxy manually:  cd <repo> && .venv/bin/python -m proxy.main"
        )

    def stop(self) -> tuple[bool, str]:
        return False, "No persistent supervisor — manually Ctrl-C the running process."

    def status(self) -> tuple[bool, str]:
        return False, "manual mode — no supervisor tracks the process"

    def logs(self, follow: bool = True) -> int:
        print("No supervisor to read logs from. Start the proxy manually to see its output.", file=sys.stderr)
        return 1

    def cleanup(self) -> None:
        """Nothing to clean up — manual mode owns no process."""
        return None

    def env_summary(self) -> dict:
        return {"mode": "manual"}


# ---- launchd (macOS) ------------------------------------------------------
# Native supervisor: a per-user LaunchAgent plist in ~/Library/LaunchAgents.
# RunAtLoad + KeepAlive means the proxy starts at login and is restarted by the
# OS if it dies — "survives reboot" for free. No admin required.
class LaunchdRuntime:
    DOMAIN = "gui"  # per-user GUI domain
    LABEL = "com.atlas.proxy"

    def __init__(self, env: RuntimeEnv, repo_root: Path, service_name: str,
                 venv_py: Path, log_file: Path) -> None:
        self.env = env
        self.repo_root = repo_root
        self.service_name = service_name
        self.venv_py = venv_py
        self.log_file = log_file
        self.label = self.LABEL
        self.info = RuntimeInfo(
            mode="launchd", label="launchd (LaunchAgent)",
            session=self.label, pid=None,
        )

    # _agent_domain returns "gui/<uid>" used by bootstrap/bootout on macOS 10.10+
    def _domain(self) -> str:
        try:
            uid = str(os.getuid())
        except AttributeError:
            uid = "0"
        return f"gui/{uid}"

    def _plist_path(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{self.label}.plist"

    def _plist(self) -> str:
        env_vars = {
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        env_block = "".join(
            f"    <key>{k}</key>\n    <string>{v}</string>\n"
            for k, v in env_vars.items()
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>\n'
            f'  <key>Label</key>\n  <string>{self.label}</string>\n'
            f'  <key>ProgramArguments</key>\n  <array>\n'
            f'    <string>{self.venv_py}</string>\n'
            '    <string>-m</string>\n    <string>proxy.main</string>\n'
            '  </array>\n'
            f'  <key>WorkingDirectory</key>\n  <string>{self.repo_root}</string>\n'
            '  <key>EnvironmentVariables</key>\n  <dict>\n'
            f'{env_block}'
            '  </dict>\n'
            '  <key>RunAtLoad</key>\n  <true/>\n'
            '  <key>KeepAlive</key>\n  <true/>\n'
            f'  <key>StandardOutPath</key>\n  <string>{self.log_file}</string>\n'
            f'  <key>StandardErrorPath</key>\n  <string>{self.log_file}</string>\n'
            '</dict></plist>\n'
        )

    def install(self) -> tuple[bool, str]:
        # Idempotent: install_runtime can call install() more than once across
        # the wizard + verify passes. macOS treats a re-bootstrap as an error
        # ("already loaded"), so guard it here.
        r = _run_quiet(["launchctl", "print", f"{self._domain()}/{self.label}"], timeout=5.0)
        if r is not None and r.returncode == 0:
            return True, f"already loaded ({self.label})"
        plist = self._plist_path()
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(self._plist())
        # (Re)load. macOS 10.15+: bootstrap gui/<uid> <plist>; older: launchctl
        # load -w. Try bootstrap first, fall back to load.
        r = _run_quiet(["launchctl", "bootstrap", self._domain(), str(plist)], timeout=10.0)
        if r is None or r.returncode != 0:
            r = _run_quiet(["launchctl", "load", "-w", str(plist)], timeout=10.0)
        if r is None or r.returncode != 0:
            return False, (r.stderr.strip() if r else "launchctl bootstrap/load failed")
        return True, f"LaunchAgent installed ({plist.name})"

    def start(self) -> tuple[bool, str]:
        # kickstart -k forces a (re)start of the job.
        r = _run_quiet(["launchctl", "kickstart", "-k", f"{self._domain()}/{self.label}"], timeout=10.0)
        if r is None or r.returncode != 0:
            # Fallback: unload+load (effectively a restart)
            _run_quiet(["launchctl", "unload", str(self._plist_path())], timeout=5.0)
            r = _run_quiet(["launchctl", "load", str(self._plist_path())], timeout=10.0)
        if r is None or r.returncode != 0:
            return False, (r.stderr.strip() if r else "launchctl start failed")
        return True, f"started via launchd ({self.label})"

    def stop(self) -> tuple[bool, str]:
        r = _run_quiet(["launchctl", "bootout", f"{self._domain()}/{self.label}"], timeout=10.0)
        if r is None or r.returncode != 0:
            # Older invocation
            r = _run_quiet(["launchctl", "unload", str(self._plist_path())], timeout=10.0)
        if r is None or r.returncode != 0:
            return False, (r.stderr.strip() if r else "launchctl stop failed")
        return True, f"stopped via launchd ({self.label})"

    def status(self) -> tuple[bool, str]:
        r = _run_quiet(["launchctl", "print", f"{self._domain()}/{self.label}"], timeout=10.0)
        if r is None:
            return False, "launchctl unavailable"
        if r.returncode != 0:
            return False, "not loaded"
        return True, "loaded (at login + KeepAlive)"

    def logs(self, follow: bool = True) -> int:
        if not self.log_file.exists():
            print(f"No log file yet: {self.log_file}", file=sys.stderr)
            return 1
        cmd = ["tail", "-n", "40"]
        if follow:
            cmd.append("-f")
        cmd.append(str(self.log_file))
        try:
            return subprocess.call(cmd)
        except FileNotFoundError:
            return 1

    def cleanup(self) -> None:
        """Best-effort: unload + remove the plist. Never raises."""
        try:
            self.stop()
        except Exception:
            pass
        try:
            self._plist_path().unlink(missing_ok=True)
        except OSError:
            pass

    def env_summary(self) -> dict:
        return {"mode": "launchd", "label": self.label, "plist": str(self._plist_path())}


# ---- Windows Task Scheduler -------------------------------------------------
# Windows uses a detached process with a repo-scoped PID file.
# Windows supervisor that starts at login and survives reboot. The task action
# runs the venv python on `proxy.main`; no admin required (per-user task).
class WindowsRuntime:
    TASK_NAME = "Atlas Proxy"

    def __init__(self, env: RuntimeEnv, repo_root: Path, service_name: str,
                 venv_py: Path, log_file: Path) -> None:
        self.env = env
        self.repo_root = repo_root
        self.service_name = service_name
        self.venv_py = venv_py
        self.log_file = log_file
        self.info = RuntimeInfo(
            mode="scheduled-task", label="detached proxy process",
            session="direct-process", pid=None,
        )

    def _action(self) -> str:
        # Run the proxy in the venv, detached, logging to file.
        return str(self.venv_py)

    def install(self) -> tuple[bool, str]:
        """Register a persistent per-user logon task, then start the proxy."""
        import subprocess

        data_dir = self.repo_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        wrapper = data_dir / "atlas-start.cmd"
        cli = self.repo_root / "atlas" / "bin" / "atlas"
        startup_log = data_dir / "startup.log"

        wrapper.write_text(
            "@echo off\r\n"
            f'cd /d "{self.repo_root}"\r\n'
            f'"{self.venv_py}" "{cli}" start >> "{startup_log}" 2>&1\r\n',
            encoding="utf-8",
            newline="",
        )

        task_action = f'cmd.exe /d /c ""{wrapper}""'

        try:
            result = subprocess.run(
                [
                    "schtasks", "/Create", "/F",
                    "/SC", "ONLOGON",
                    "/TN", self.TASK_NAME,
                    "/TR", task_action,
                    "/RL", "LIMITED",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )

            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip()
                return False, f"Could not register logon task: {detail}"

        except Exception as exc:
            return False, f"Could not register logon task: {exc}"

        ok, message = self.start()
        if not ok:
            return False, f"Logon task registered, but proxy failed to start: {message}"

        return True, f"Logon task registered; {message}"

    def _schtasks(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess | None:
        return _run_quiet(["schtasks", *args], timeout=10.0)

    def start(self) -> tuple[bool, str]:
        import subprocess
        from pathlib import Path

        pidfile = Path(self.repo_root) / "data" / "run.pid"
        if self.status()[0]:
            return True, f"already running (pid={pidfile.read_text().strip()})"

        pidfile.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            with self.log_file.open("ab") as log:
                flags = (
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
                proc = subprocess.Popen(
                    [str(self.venv_py), "-m", "proxy.main"],
                    cwd=str(self.repo_root),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                    close_fds=True,
                )
            pidfile.write_text(str(proc.pid), encoding="utf-8")
            return True, f"started (pid={proc.pid}, log={self.log_file})"
        except Exception as exc:
            return False, f"could not start proxy: {type(exc).__name__}: {exc}"

    def stop(self) -> tuple[bool, str]:
        import subprocess
        from pathlib import Path

        pidfile = Path(self.repo_root) / "data" / "run.pid"
        try:
            pid = int(pidfile.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pidfile.unlink(missing_ok=True)
            return True, "not running"

        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                # Do not remove the PID file if stopping failed.
                return False, (
                    result.stderr.strip() or result.stdout.strip()
                    or f"could not stop PID {pid}"
                )
            pidfile.unlink(missing_ok=True)
            return True, f"stopped PID {pid}"
        except Exception as exc:
            return False, f"could not stop PID {pid}: {exc}"

    def status(self) -> tuple[bool, str]:
        import csv
        import io
        from pathlib import Path

        pidfile = Path(self.repo_root) / "data" / "run.pid"
        try:
            pid = int(pidfile.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pidfile.unlink(missing_ok=True)
            return False, "not running"

        # Do not use os.kill(pid, 0) on Windows: Windows signal handling
        # differs from POSIX and is not a safe process-existence check.
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"could not verify PID {pid}: {exc}"

        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return False, detail or f"could not verify PID {pid}"

        try:
            rows = csv.reader(io.StringIO(result.stdout))
            running = any(
                len(row) > 1 and row[1].strip() == str(pid)
                for row in rows
            )
        except (csv.Error, OSError) as exc:
            return False, f"could not parse tasklist output: {exc}"

        if running:
            return True, f"running (pid={pid})"

        pidfile.unlink(missing_ok=True)
        return False, "not running"

    def logs(self, follow: bool = True) -> int:
        import time

        if not self.log_file.exists():
            print(f"No log file yet: {self.log_file}", file=sys.stderr)
            return 1

        try:
            with self.log_file.open("r", encoding="utf-8", errors="replace") as log:
                lines = log.readlines()
                print("".join(lines[-40:]), end="")
                if follow:
                    while True:
                        line = log.readline()
                        if line:
                            print(line, end="", flush=True)
                        else:
                            time.sleep(0.25)
        except KeyboardInterrupt:
            return 0
        except OSError as exc:
            print(f"Could not read log: {exc}", file=sys.stderr)
            return 1
        return 0

    def cleanup(self) -> None:
        from pathlib import Path

        try:
            self.stop()
        except Exception:
            pass
        (Path(self.repo_root) / "data" / "run.pid").unlink(missing_ok=True)

    def env_summary(self) -> dict:
        return {"mode": "scheduled-task", "runtime": "detached-process"}


def run_powershell_local(script: str, timeout: float = 30.0) -> subprocess.CompletedProcess | None:
    """One-off PowerShell invocation used by the Windows runtime.

    Kept here (next to the only caller) so runtime.py stays import-safe on
    POSIX — the WindowsRuntime path is only taken on Windows.
    """
    import shutil as _sh
    ps = _sh.which("pwsh") or _sh.which("powershell") or _sh.which("powershell.exe")
    if not ps:
        return None
    try:
        return subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "ByPass",
             "-Command", script],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


# ---------------------------------------------------------------------------
# Persistence: data/runtime.json
# ---------------------------------------------------------------------------

RUNTIME_FILE_NAME = "runtime.json"


def _runtime_path(repo_root: Path) -> Path:
    return repo_root / "data" / RUNTIME_FILE_NAME


def load_runtime_choice(repo_root: Path) -> dict | None:
    """Read the previously chosen runtime — None if not yet installed."""
    p = _runtime_path(repo_root)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save_runtime_choice(repo_root: Path, env: RuntimeEnv, mode: RuntimeMode,
                        info: dict) -> None:
    """Persist the install-time choice for subsequent commands."""
    p = _runtime_path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": mode,
        "env": asdict(env),
        "info": info,
        "installed_at": int(time.time()),
    }
    p.write_text(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Factory: build the right Runtime for the current situation
# ---------------------------------------------------------------------------

def _runtime_paths(repo_root: Path, venv_py: Path):
    """Compute per-runtime paths (pidfile, log file) — kept centralised."""
    data_dir = repo_root / "data"
    return {
        "pid_file": data_dir / "run.pid",
        "log_file": data_dir / "run.log",
        "venv_py": venv_py,
        "repo_root": repo_root,
    }


def install_runtime(repo_root: Path, venv_py: Path,
                   service_name: str) -> tuple[RuntimeMode, bool, str]:
    """Detect, attempt, verify, fallback. Only persist a runtime after a
    successful health check.

    The flow is:
      1. Detect the environment.
      2. Build an ordered list of runtime candidates.
      3. For each candidate: install (or start), then poll the proxy's
         health endpoint with a short bounded budget.
      4. If health succeeds: persist the choice and return.
      5. If health fails: cleanup the candidate and try the next.
      6. If every candidate fails: persist ``manual`` and return success
         so the installer itself never fails on a runtime problem.
    """
    env = detect_env()
    candidates = _candidate_modes(env)

    def _emit(msg: str) -> None:
        # Progress lines are consumed by the shell installer (which indents
        # them) and shown raw when invoked from the Python wizard.
        print(msg, flush=True)

    _emit(f"Detecting runtime — candidates: {', '.join(candidates) or 'none'}")

    last_message = "no runtime candidates available"
    first = True
    for mode in candidates:
        if first:
            _emit(f"Runtime: {mode}")
            first = False
        else:
            _emit(f"Falling back: {mode}")
        runtime = build_runtime(mode, env, repo_root, venv_py, service_name)
        installer = getattr(runtime, "install", None)
        try:
            ok, message = installer() if callable(installer) else runtime.start()
        except Exception as exc:  # installer crashed — treat as failure
            ok, message = False, f"{type(exc).__name__}: {exc}"

        if not ok:
            _emit(f"  ! {mode} failed: {message}")
            # Best-effort cleanup before falling back. Never raises.
            try:
                runtime.cleanup()
            except Exception:
                pass
            last_message = f"{mode}: {message}"
            continue

        # Install/start succeeded — now verify the proxy is actually healthy.
        healthy, health_msg = check_health(repo_root=repo_root)
        if not healthy:
            _emit(f"  ! {mode} unhealthy: {health_msg}")
            try:
                runtime.cleanup()
            except Exception:
                pass
            last_message = f"{mode}: {health_msg}"
            continue

        _emit(f"  ✓ {mode} healthy: {health_msg}")
        save_runtime_choice(
            repo_root, env, mode, runtime.info.__dict__,
        )
        return mode, True, health_msg or message

    # Windows has one direct-process runtime; do not report success if it failed.
    if env.os == "windows":
        _emit(f"! Windows proxy startup failed: {last_message}")
        return "scheduled-task", False, last_message

    # Everything failed — record manual so the installer still completes.
    _emit("! No automatic runtime manager available")
    manual_rt = build_runtime("manual", env, repo_root, venv_py, service_name)
    save_runtime_choice(
        repo_root, env, "manual", manual_rt.info.__dict__,
    )
    return "manual", True, (
        f"no runtime worked ({last_message}); persisted manual mode"
    )


# Health endpoint + bounded polling window. A few seconds is plenty for
# uvicorn to bind; we don't want the installer sitting around for minutes.
#
# NOTE: the fork's default LISTEN_PORT is 8777 (prod lives on 8788).
# We prefer reading it from proxy.config so we always check the right port;
# a hard-coded constant is only used as a last-resort fallback.
HEALTH_PORT_DEFAULT = 8777
HEALTH_TIMEOUT_S = 6.0
HEALTH_POLL_S = 0.25


def _health_url(repo_root: Path) -> str:
    """Resolve the actual proxy port for this repo.

    Priority:
      1. ``data/bound_port`` — written by the running proxy with the port
         it actually bound to. Honors port-collision fallback at runtime.
      2. ``proxy.config.LISTEN_PORT`` — the configured default (fork: 8777,
         NOT prod's 8788).
      3. The fork's compiled-in default.
    """
    port: int | None = None
    bp = repo_root / "data" / "bound_port"
    if bp.exists():
        try:
            v = int(bp.read_text().strip())
            if 1 <= v <= 65535:
                port = v
        except (ValueError, OSError):
            pass
    if port is None:
        try:
            sys.path.insert(0, str(repo_root))
            from proxy.config import LISTEN_PORT  # type: ignore
            port = int(LISTEN_PORT)
        except Exception:
            pass
    if port is None:
        port = HEALTH_PORT_DEFAULT
    return f"http://127.0.0.1:{port}/health"


def check_health(timeout_s: float = HEALTH_TIMEOUT_S,
                 url: str | None = None,
                 repo_root: Path | None = None) -> tuple[bool, str]:
    """Poll the proxy health endpoint with a short bounded budget.

    Returns (True, "ok") on success or (False, reason) on timeout/error.
    Uses urllib so we don't pull requests into the installer path.
    """
    import socket
    import urllib.request
    import urllib.error

    if url is None:
        if repo_root is None:
            url = f"http://127.0.0.1:{HEALTH_PORT_DEFAULT}/health"
        else:
            url = _health_url(repo_root)

    deadline = time.monotonic() + timeout_s
    last_err = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                if 200 <= resp.status < 300:
                    return True, f"health check passed ({url})"
                last_err = f"status {resp.status}"
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last_err = getattr(exc, "reason", None) or str(exc) or "unreachable"
        except socket.timeout:
            last_err = "timeout"
        time.sleep(HEALTH_POLL_S)
    return False, f"health check failed: {last_err or 'no response'} ({url})"


def _candidate_modes(env: RuntimeEnv) -> list[RuntimeMode]:
    """Build the ordered list of runtime candidates for this environment.

    Linux:  systemd-system -> systemd-user -> tmux -> nohup
    macOS:  launchd -> tmux -> nohup
    Windows: detached process -> manual
    Other:  tmux -> nohup
    Manual is always the final fallback (handled by the caller).
    """
    if env.os == "linux":
        candidates: list[RuntimeMode] = []
        if env.systemd_system_usable:
            candidates.append("systemd")
        if env.systemd_user_usable:
            candidates.append("systemd-user")
        if env.tmux_usable:
            candidates.append("tmux")
        if env.nohup_usable:
            candidates.append("nohup")
        return candidates
    if env.os == "macos":
        candidates = []
        if env.launchd_usable:
            candidates.append("launchd")
        if env.tmux_usable:
            candidates.append("tmux")
        if env.nohup_usable:
            candidates.append("nohup")
        return candidates
    if env.os == "windows":
        return ["scheduled-task"]
    candidates = []
    if env.tmux_usable:
        candidates.append("tmux")
    if env.nohup_usable:
        candidates.append("nohup")
    return candidates


def build_runtime(mode: RuntimeMode, env: RuntimeEnv, repo_root: Path,
                  venv_py: Path, service_name: str) -> Runtime:
    """Construct the Runtime implementation for the chosen mode."""
    paths = _runtime_paths(repo_root, venv_py)
    if mode == "systemd":
        return SystemdRuntime(env, repo_root, service_name, paths["venv_py"])
    if mode == "systemd-user":
        return SystemdUserRuntime(env, repo_root, service_name, paths["venv_py"])
    if mode == "tmux":
        tmux_bin = env.tmux_bin or shutil.which("tmux") or "tmux"
        return TmuxRuntime(env, repo_root, tmux_bin, paths["venv_py"],
                           paths["log_file"])
    if mode == "nohup":
        return NohupRuntime(env, repo_root, paths["venv_py"],
                            paths["pid_file"], paths["log_file"])
    if mode == "manual":
        return ManualRuntime()
    if mode == "launchd":
        return LaunchdRuntime(env, repo_root, service_name,
                              paths["venv_py"], paths["log_file"])
    if mode == "scheduled-task":
        return WindowsRuntime(env, repo_root, service_name,
                              paths["venv_py"], paths["log_file"])
    raise ValueError(f"unknown runtime mode: {mode!r}")


def get_runtime(repo_root: Path, venv_py: Path, service_name: str) -> Runtime:
    """Pick the active Runtime for subsequent commands.

    Strategy:
      1. If `data/runtime.json` exists AND the persisted mode is still
         actually usable on this host, honour it.
      2. Otherwise re-detect from the environment and pick the best
         available mode (with ``manual`` as the last-resort fallback).

    This is intentionally forgiving: a host that previously had
    systemd-user but lost its user-bus between installs will not get
    stuck trying to use a runtime that no longer exists.
    """
    persisted = load_runtime_choice(repo_root)
    env = detect_env()

    # Windows uses the repo-scoped direct-process runtime.
    if env.os == "windows":
        return build_runtime(
            "scheduled-task", env, repo_root, venv_py, service_name
        )

    if persisted:
        mode = persisted.get("mode", "nohup")
        if _mode_usable(mode, env):
            return build_runtime(mode, env, repo_root, venv_py, service_name)

    # Re-detect from the live environment.
    candidates = _candidate_modes(env)
    for mode in candidates:
        if _mode_usable(mode, env):
            return build_runtime(mode, env, repo_root, venv_py, service_name)

    return build_runtime("manual", env, repo_root, venv_py, service_name)


def _mode_usable(mode: RuntimeMode, env: RuntimeEnv) -> bool:
    """Is this mode actually usable on the current host right now?"""
    if mode == "systemd":
        return env.systemd_system_usable
    if mode == "systemd-user":
        return env.systemd_user_usable
    if mode == "tmux":
        return env.tmux_usable
    if mode == "nohup":
        return env.nohup_usable
    if mode == "launchd":
        return env.launchd_usable
    if mode == "scheduled-task":
        return env.os == "windows"
    if mode == "manual":
        return True
    return False


# ---------------------------------------------------------------------------
# Tiny helpers used by the wizard
# ---------------------------------------------------------------------------

def shlex_quote(s: str) -> str:
    """Quote a string for safe inclusion in a shell command."""
    import shlex
    return shlex.quote(s)
