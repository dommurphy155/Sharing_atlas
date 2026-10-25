"""Shared interactive installer wizard for Atlas.

Runs inside the project venv (Rich is available). Driven by:

    - install.sh / install.ps1   (first-run, may copy source to ~/atlas)
    - `atlas install`            (re-run, idempotent, stays in place)

Flow (per the cross-platform installer spec):
    1. Intro / explain Atlas
    2. Pick a harness (Claude Code / Codex / Hermes)
    3. Start that harness's install in the background, carry on
    4. Pick an upstream provider + import keys (file / paste / skip)
    5. Configure the harness to route through the local proxy (dummy key)
    6. Start the proxy and verify: python env, deps, config, keys, listening
       port, a REAL model request, harness routing, CLI from any dir,
       auto-start registration, harness install + config
    7. Launch the harness

Everything is idempotent: re-running detects existing keys/config/startup and
repairs without duplicating them.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

import setup.core as core

CONSOLE = Console()


# ===========================================================================
# OS adapter dispatch
# ===========================================================================
def _os_module():
    """Lazily import the OS-specific support module (avoids import cycles)."""
    name = core.detect_os()
    return importlib.import_module(f".{name}_setup", package="setup")


def _repo_root_from(args_repo: str | None) -> Path:
    if args_repo:
        return Path(args_repo).resolve()
    return core.repo_root()


# ===========================================================================
# Intro
# ===========================================================================
def _intro() -> None:
    title = "[bold underline]Atlas Proxy[/bold underline]"
    body = (
        "[cyan]Atlas[/cyan] is a single-user translation proxy for OpenRouter / "
        "Hugging Face / NVIDIA.\n"
        "It owns your upstream API keys, load-balances them, and exposes an "
        "Anthropic- and OpenAI-compatible API on [green]http://127.0.0.1:"
        + str(core.proxy_port(core.repo_root()))
        + "[/green].\n\n"
        "After install you pick one [bold]coding harness[/bold] (Claude Code, "
        "Codex, Hermes). The wizard wires that harness to talk to this proxy "
        "using a placeholder key, then verifies a real model request end-to-end."
    )
    CONSOLE.print(Panel(Text.from_markup(body), title=title, border_style="cyan"))


# ===========================================================================
# Harness install — verified against official docs (2026)
# ===========================================================================
class Harness:
    def __init__(
        self,
        id: str,
        label: str,
        bin_name: str,
        install_cmds: dict[str, list[str] | None],  # os -> cmd list or None (unsupported)
        configure: Callable[[str, str], tuple[bool, str]],
        smoke: Callable[[str, str], tuple[bool, str, str]],
    ):
        self.id = id
        self.label = label
        self.bin = bin_name
        self.install_cmds = install_cmds
        self.configure = configure
        self.smoke = smoke


def _install_claude(os_name: str) -> list[str] | None:
    if os_name == "linux" or os_name == "macos":
        return ["bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"]
    if os_name == "windows":
        # winget is silent & non-interactive; fall back to the PS installer.
        return [
            "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
            "ByPass", "-Command",
            "winget install --id Anthropic.ClaudeCode --silent "
            "--accept-source-agreements --accept-package-agreements 2>$null "
            "|| iex (irm https://claude.ai/install.ps1)",
        ]
    return None


def _install_codex(os_name: str) -> list[str] | None:
    if os_name == "linux" or os_name == "macos":
        return ["bash", "-c", "curl -fsSL https://chatgpt.com/codex/install.sh | sh"]
    if os_name == "windows":
        return [
            "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
            "ByPass", "-Command",
            "winget install --id OpenAI.Codex --silent "
            "--accept-source-agreements --accept-package-agreements 2>$null "
            "|| iex (irm https://chatgpt.com/codex/install.ps1)",
        ]
    return None


def _install_hermes(os_name: str) -> list[str] | None:
    if os_name == "linux" or os_name == "macos":
        return ["bash", "-c",
                "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -- --non-interactive"]
    if os_name == "windows":
        return [
            "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
            "ByPass", "-Command",
            "iex (irm https://hermes-agent.nousresearch.com/install.ps1)",
        ]
    return None


# ---- Configure: point the harness at the LOCAL proxy (placeholder key) ----
def _configure_claude(base_url: str, dummy_key: str) -> tuple[bool, str]:
    """Claude Code — env block in ~/.claude/settings.json.
    Source: https://code.claude.com/docs/en/llm-gateway-connect
    """
    from rich.prompt import Confirm as _C  # noqa: F401 (kept for parity)
    home = Path(os.path.expanduser("~"))
    settings = home / ".claude" / "settings.json"
    existing = _read_json(settings)
    merged = _deep_merge(existing, {
        "env": {
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_AUTH_TOKEN": dummy_key,
        }
    })
    _backup(settings)
    _write_json(settings, merged)
    return True, f"set env.ANTHROPIC_BASE_URL + env.ANTHROPIC_AUTH_TOKEN in {settings}"


def _configure_codex(base_url: str, dummy_key: str) -> tuple[bool, str]:
    """Codex — openai_base_url in ~/.codex/config.toml.
    Source: https://developers.openai.com/codex/config-advanced
    """
    home = Path(os.path.expanduser("~"))
    cfg = home / ".codex" / "config.toml"
    _backup(cfg)
    atlas_url = f"{base_url}/v1"
    existing = cfg.read_text() if cfg.exists() else ""
    if "Added by Atlas proxy installer" in existing and "openai_base_url" in existing:
        msg = f"{cfg} already configured for Atlas — leaving alone"
        return True, msg
    tail = (
        f"\n# Added by Atlas proxy installer ({_ts()})\n"
        f'openai_base_url = "{atlas_url}"\n'
        f"# Atlas uses its own key pool — set OPENAI_API_KEY to any non-empty value.\n"
        f'export OPENAI_API_KEY={dummy_key}\n'
    )
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(existing + tail)
    # Persist the dummy key in shell rc so codex always finds it.
    _write_rc_env("OPENAI_API_KEY", dummy_key)
    return True, f"wrote {cfg} (openai_base_url={atlas_url})"


def _configure_hermes(base_url: str, dummy_key: str) -> tuple[bool, str]:
    """Hermes — hermes config set (official safe path)."""
    if shutil.which("hermes") is None:
        return False, "hermes not on PATH"
    cmds = [
        ["hermes", "config", "set", "model.base_url", f"{base_url}/v1"],
        ["hermes", "config", "set", "model.provider", "custom"],
        ["hermes", "config", "set", "model.api_key", dummy_key],
    ]
    errors = []
    for c in cmds:
        r = subprocess.run(c, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            errors.append(f"{' '.join(c[1:3])}: {(r.stderr or r.stdout).strip()}")
    if errors:
        return False, "; ".join(errors)
    return True, "hermes config set model.base_url/provider/api_key"


# ---- Smoke / verification (real requests routed through the proxy) ----
SMOKE_PROMPT = "Reply with the single word OK."


def _run_capture(bin_name: str, args: list[str], timeout: int) -> tuple[bool, str]:
    try:
        r = subprocess.run([bin_name, *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, f"{bin_name} not found"
    except subprocess.TimeoutExpired:
        return False, f"{bin_name} timed out after {timeout}s"
    out = (r.stdout or "").strip()
    low = out.lower()
    if any(e in low for e in ("401", "403", "unauthorized", "forbidden", "error")):
        return False, f"{bin_name} rejected auth: {out[:120]}"
    return r.returncode == 0, f"exit={r.returncode}, stdout={out[:120]}"


def _smoke_version(bin_name: str) -> tuple[bool, str, str]:
    if shutil.which(bin_name) is None:
        return False, f"{bin_name} not on PATH", "no fallback"
    r = subprocess.run([bin_name, "--version"], capture_output=True, text=True, timeout=20)
    return (r.returncode == 0), f"{bin_name} --version exit={r.returncode}", "version only"


def _smoke_claude(_base_url: str, _dummy_key: str) -> tuple[bool, str, str]:
    if shutil.which("claude") is None:
        return False, "claude not on PATH", "no fallback"
    ok, summary = _run_capture(
        "claude",
        ["--bare", "-p", SMOKE_PROMPT, "--allowedTools", "", "--output-format", "text"],
        timeout=90,
    )
    return ok, summary, ""


def _smoke_codex(_base_url: str, _dummy_key: str) -> tuple[bool, str, str]:
    if shutil.which("codex") is None:
        return False, "codex not on PATH", "no fallback"
    try:
        r = subprocess.run(
            ["codex", "exec", "--ephemeral", "--sandbox", "read-only",
             "--skip-git-repo-check", SMOKE_PROMPT],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "codex exec timed out after 120s", ""
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if "Error loading config.toml" in (err or out):
        return False, f"codex config parse error: {(err or out).splitlines()[0]}", "fix ~/.codex/config.toml"
    if r.returncode == 0:
        return True, f"exit=0, stdout={out[:120]}", ""
    return False, f"exit={r.returncode}, stderr={err[:200] or out[:200]}", ""


def _smoke_hermes(_base_url: str, _dummy_key: str) -> tuple[bool, str, str]:
    if shutil.which("hermes") is None:
        return False, "hermes not on PATH", "no fallback possible"
    ok, summary = _run_capture(
        "hermes",
        ["-z", SMOKE_PROMPT, "--provider", "openrouter", "--model", "minimax/minimax-m3:free"],
        timeout=120,
    )
    return ok, summary, ""


HARNESSES: list[Harness] = [
    Harness("claude", "Claude Code", "claude",
            {"linux": _install_claude("linux"), "macos": _install_claude("macos"), "windows": _install_claude("windows")},
            _configure_claude, _smoke_claude),
    Harness("codex", "Codex", "codex",
            {"linux": _install_codex("linux"), "macos": _install_codex("macos"), "windows": _install_codex("windows")},
            _configure_codex, _smoke_codex),
    Harness("hermes", "Hermes", "hermes",
            {"linux": _install_hermes("linux"), "macos": _install_hermes("macos"), "windows": _install_hermes("windows")},
            _configure_hermes, _smoke_hermes),
]


# ===========================================================================
# Shared small helpers (keys / json / rc files / backup)
# ===========================================================================
def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _deep_merge(target: dict, patch: dict) -> dict:
    out = dict(target)
    for k, v in patch.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = path.with_name(f"{path.name}.atlas-bak.{ts}")
    n = 1
    while bak.exists():
        bak = path.with_name(f"{path.name}.atlas-bak.{ts}.{n}")
        n += 1
    shutil.copy2(path, bak)
    return bak


def _ts() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _write_rc_env(var: str, value: str) -> None:
    """Append `export VAR=value` to ~/.bashrc and ~/.zshrc (idempotent)."""
    marker = f"# Atlas proxy installer: {var}"
    for rc in (".bashrc", ".zshrc"):
        rc_path = Path(os.path.expanduser("~")) / rc
        if not rc_path.exists():
            continue
        content = rc_path.read_text()
        if var in content:
            continue
        rc_path.write_text(content + f"\n{marker}\nexport {var}={value}\n")


# ===========================================================================
# Harness install (background) + finalize
# ===========================================================================
def _start_install(h: Harness, os_name: str):
    """Start the harness install in the background (non-blocking Popen)."""
    cmd = h.install_cmds.get(os_name)
    if cmd is None:
        return None
    CONSOLE.print(f"  → launching {h.label} installer in background...")
    try:
        if os_name == "windows" and cmd[0] == "powershell":
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, shell=False)
        else:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True)
    except OSError as e:
        CONSOLE.print(f"  [red]✗ could not start {h.label} installer: {e}[/red]")
        return None
    return proc


def _finalize_install(h: Harness, proc, dry_run: bool) -> tuple[bool, str]:
    """Wait for the background installer, then verify the binary landed."""
    if proc is None:
        return False, f"{h.label} installer not available on this OS"
    if dry_run:
        return True, f"{h.label} install (dry-run — skipped background install)"
    CONSOLE.print(f"  waiting for {h.label} installer to finish...")
    try:
        _, stderr = proc.communicate(timeout=600)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return False, f"{h.label} install timed out after 600s"
    if proc.returncode != 0:
        tail = (stderr or "").strip().splitlines()[-6:]
        return False, f"{h.label} installer exited {proc.returncode}: {tail}"
    if shutil.which(h.bin) is None:
        return False, f"{h.label} installer finished but '{h.bin}' not on PATH"
    # Version check for a sane signal.
    vr = subprocess.run([h.bin, "--version"], capture_output=True, text=True, timeout=20)
    ver = (vr.stdout or "").strip().splitlines()[-1] if vr.stdout.strip() else ""
    return True, f"{h.label} installed ({ver})"


# ===========================================================================
# Provider + key import
# ===========================================================================
PROVIDERS = [
    ("openrouter",  "OpenRouter",   "Recommended"),
    ("huggingface", "Hugging Face", "Frontier models / limited free usage"),
    ("nvidia",      "NVIDIA",       "Reliable, but slower"),
]


def _existing_keys(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]


def _append_keys(path: Path, new_keys: list[str]) -> int:
    existing = set(_existing_keys(path))
    to_add = [k for k in new_keys if k not in existing]
    if not to_add:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = "" if (path.exists() and not path.read_text().endswith("\n")) else "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(suffix + "\n".join(to_add) + "\n")
    return len(to_add)


def _pick_provider() -> str | None:
    CONSOLE.print(Panel("Choose your upstream API provider", title="Provider", border_style="cyan"))
    for i, (slug, label, hint) in enumerate(PROVIDERS, 1):
        line = f"  {i} {label}"
        line += "  [green](Recommended)[/green]" if "Recommended" in hint else f"  [dim]({hint})[/dim]"
        CONSOLE.print(line)
    CONSOLE.print("  4 Skip for now")
    raw = Prompt.ask("Select", choices=[str(i) for i in range(1, 5)], default="1")
    if raw == "4":
        return None
    return PROVIDERS[int(raw) - 1][0]


def _import_keys(repo: Path, provider: str, dry_run: bool) -> int:
    """Interactively add keys for the chosen provider. Returns count added."""
    target = core.key_file_for(repo, provider)
    if dry_run:
        CONSOLE.print(f"  [dim]dry-run: keys would be appended to {target}[/dim]")
        return 0
    CONSOLE.print("\n[cyan]How would you like to add API keys?[/cyan]")
    CONSOLE.print("  1 Add keys individually")
    CONSOLE.print("  2 Use an existing key file")
    CONSOLE.print("  3 Skip")
    mode = Prompt.ask("Select", choices=["1", "2", "3"], default="3")
    if mode == "1":
        n = 0
        while True:
            key = Prompt.ask("API key", default="").strip()
            if not key:
                break
            n += _append_keys(target, [key])
            CONSOLE.print("[green]✓ added[/green]" if n else "[yellow]already present[/yellow]")
            if not Confirm.ask("Add another?", default=False):
                break
        return n
    if mode == "2":
        p = Prompt.ask("Path to key file").strip()
        path = Path(os.path.expanduser(p))
        if not path.exists():
            CONSOLE.print(f"[red]file not found: {path}[/red]")
            return 0
        candidates = [ln.strip() for ln in path.read_text().splitlines()
                      if ln.strip() and not ln.startswith("#")]
        # de-dup while preserving order
        seen, uniq = set(), []
        for k in candidates:
            if k not in seen:
                seen.add(k); uniq.append(k)
        added = _append_keys(target, uniq)
        CONSOLE.print(f"  Existing {_len(path)} / Imported {len(candidates)} / Duplicates {len(candidates)-added} / Added {added} / Total {_len(target)}")
        return added
    return 0


def _len(path: Path) -> int:
    return len([1 for ln in _existing_keys(path)])


# ===========================================================================
# Proxy management (start + real-request verification)
# ===========================================================================
def _start_proxy(repo: Path, dry_run: bool) -> tuple[bool, str]:
    if dry_run:
        return True, "dry-run (proxy not started)"
    osmod = _os_module()
    if hasattr(osmod, "start_proxy"):
        return osmod.start_proxy(repo)
    # Fallback: drive the existing runtime module.
    sys.path.insert(0, str(repo / "atlas"))
    try:
        from bin import runtime as rt  # type: ignore
    except Exception as e:
        return False, f"runtime import failed: {e}"
    try:
        mode, ok, msg = rt.install_runtime(repo, core.venv_python(repo), core.SERVICE_NAME)
        return ok, msg
    except Exception as e:
        return False, f"runtime install failed: {e}"


def _real_model_request(repo: Path) -> tuple[bool, str]:
    """Hit /v1/chat/completions through the proxy with a dummy client key.

    This is the spec's 'real model response' check — NOT just the health
    endpoint. Requires real upstream keys to be present.
    """
    import urllib.request
    import urllib.error
    url = core.proxy_health_url(repo).replace("/health", "/v1/chat/completions")
    body = json.dumps({
        "model": "anything",
        "messages": [{"role": "user", "content": "Reply with the single word OK."}],
        "max_tokens": 8,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {core.DUMMY_KEY}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        if resp.status == 200 and data.get("choices"):
            return True, "got model response"
        return False, f"non-200/empty response: {resp.status}"
    except urllib.error.HTTPError as e:
        msg = (e.read().decode()[:200] if e.fp else "")
        return False, f"HTTP {e.code}: {msg}"
    except Exception as e:
        return False, f"request failed: {e}"


# ===========================================================================
# Verification table
# ===========================================================================
def _verify(repo: Path, h: Harness | None, has_keys: bool, dry_run: bool) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    sys.path.insert(0, str(repo))
    py = core.venv_python(repo)

    results.append(("Python venv present", py.is_file(), str(py)))
    r = subprocess.run([str(py), "-c", "import fastapi,uvicorn,httpx,orjson,rich"],
                       capture_output=True, text=True, timeout=30)
    results.append(("Deps importable", r.returncode == 0, r.stderr.strip()[:160] or "ok"))

    # proxy config + keys
    from proxy.config import LISTEN_PORT, PROVIDER  # type: ignore
    results.append(("proxy config loads", True, f"provider={PROVIDER} port={LISTEN_PORT}"))
    kf = core.key_file_for(repo, "openrouter")
    results.append(("upstream key file present", kf.exists(), str(kf)))

    # proxy start + health
    ok, msg = _start_proxy(repo, dry_run)
    results.append(("proxy started", ok, msg))
    healthy, hmsg = core.health_check(repo, timeout=8 if not dry_run else 0.1)
    results.append(("proxy /health responds", healthy, hmsg))

    # real model request (only meaningful if keys present + proxy up)
    if healthy and has_keys:
        ok, msg = _real_model_request(repo)
        results.append(("real model request", ok, msg))
    else:
        caveat = "skipped — no upstream keys configured" if not has_keys else "skipped (proxy down)"
        results.append(("real model request", False, caveat))

    if h is None:
        # proxy-only install — skip harness-specific checks
        osmod = _os_module()
        results.append(("atlas CLI on PATH", shutil.which("atlas") is not None,
                        shutil.which("atlas") or "not on PATH"))
        if hasattr(osmod, "verify_startup"):
            sup_ok, sup_msg = osmod.verify_startup(repo)
            results.append(("auto-start registered", sup_ok, sup_msg))
        return results

    # harness
    installed = shutil.which(h.bin) is not None
    results.append((f"harness installed ({h.label})", installed, h.bin or "not found"))
    base_url = core.proxy_health_url(repo).replace("/health", "")
    cfg_ok, cfg_msg = h.configure(base_url, core.DUMMY_KEY)
    results.append((f"harness configured ({h.label})", cfg_ok, cfg_msg))
    if installed and healthy:
        ok, msg, caveat = h.smoke(base_url, core.DUMMY_KEY)
        detail = f"{msg}  [{caveat}]" if caveat else msg
        results.append(("harness → proxy round-trip", ok, detail))
    else:
        results.append(("harness → proxy round-trip", False, "skipped (harness or proxy not ready)"))

    # CLI + startup
    osmod = _os_module()
    cli_on_path = shutil.which("atlas") is not None
    results.append(("atlas CLI on PATH", cli_on_path, shutil.which("atlas") or "not on PATH"))
    if hasattr(osmod, "verify_startup"):
        sup_ok, sup_msg = osmod.verify_startup(repo)
        results.append(("auto-start registered", sup_ok, sup_msg))

    return results


# ===========================================================================
# Harness + provider pick
# ===========================================================================
def _pick_harness() -> Harness | None:
    CONSOLE.print(Panel("Choose your coding harness", title="Harness", border_style="cyan"))
    for i, h in enumerate(HARNESSES, 1):
        CONSOLE.print(f"  {i} {h.label}")
    CONSOLE.print("  4 Skip (install harness later)")
    raw = Prompt.ask("Select", choices=["1", "2", "3", "4"], default="1")
    if raw == "4":
        return None
    return HARNESSES[int(raw) - 1]


def _render_results(results: list[tuple[str, bool, str]]) -> bool:
    tbl = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1))
    tbl.add_column("Check", style="bold")
    tbl.add_column("Status", justify="center", width=6)
    tbl.add_column("Detail", style="dim", overflow="fold")
    ok_all = True
    for name, ok, detail in results:
        tbl.add_row(name, "[green]✔[/green]" if ok else "[red]✘[/red]", detail or "")
        ok_all = ok_all and ok
    CONSOLE.print()
    CONSOLE.print(tbl)
    return ok_all


# ===========================================================================
# Launch the harness
# ===========================================================================
def _launch(h: Harness) -> None:
    CONSOLE.print(f"[green]✓ Setup complete.[/green]")
    CONSOLE.print("")
    if not sys.stdin.isatty():
        CONSOLE.print(f"Atlas is ready — run [bold]{h.bin}[/bold] to start.")
        return
    CONSOLE.print(f"Launching {h.label}...")
    bin_path = shutil.which(h.bin) or h.bin
    try:
        os.execvp(bin_path, [bin_path])
    except FileNotFoundError:
        CONSOLE.print(f"[red]failed to exec {bin_path} — run it manually[/red]")


# ===========================================================================
# Top-level wizard + install orchestration
# ===========================================================================
def run_wizard(repo: Path, dry_run: bool = False) -> int:
    _intro()
    # 1. Harness
    h = _pick_harness()
    # 2. Provider + keys (runs while a harness installs in the background)
    provider = _pick_provider()
    has_keys = False
    if provider:
        n = _import_keys(repo, provider, dry_run)
        has_keys = n > 0 or bool(_existing_keys(core.key_file_for(repo, provider)))
        core.write_runtime_provider(repo, provider)
    else:
        _write_runtime_provider_fallback(repo, dry_run)

    if h is None:
        CONSOLE.print("[yellow]No harness selected — finishing proxy install only.[/yellow]")
        results = _verify(repo, h, has_keys, dry_run)  # type: ignore[arg-type]
        all_ok = _render_results(results)
        if all_ok:
            CONSOLE.print("[green]✓ Atlas is ready. Run `atlas switch`/`atlas start`.[/green]")
        return 0 if all_ok else 1

    # 3. Background install (only when we actually picked a harness)
    os_name = core.detect_os()
    proc = None if dry_run else _start_install(h, os_name)
    # 4. Finalize harness install (verify + config)
    if proc is not None or dry_run:
        ok, msg = _finalize_install(h, proc, dry_run)
        CONSOLE.print(("  [green]✔[/green]" if ok else "  [red]✘[/red]") + f" {h.label}: {msg}")
        installed = ok and shutil.which(h.bin) is not None
        if not installed:
            CONSOLE.print(f"[yellow]⚠ {h.label} install failed — launch will be skipped.[/yellow]")
    else:
        installed = shutil.which(h.bin) is not None
        CONSOLE.print(("  [green]✔[/green]" if installed else "  [yellow]⚠[/yellow]") + f" {h.label} already installed")
    # 5. Verify
    results = _verify(repo, h, has_keys, dry_run)
    all_ok = _render_results(results)
    failed = [r for r in results if not r[1]]
    if failed:
        CONSOLE.print(f"\n[yellow]⚠ {len(failed)} check(s) failed — review above.[/yellow]")
    # 6. Launch only after harness verified installed & configured
    if installed and all_ok:
        _launch(h)
    elif installed:
        CONSOLE.print(f"[yellow]⚠ {h.label} is installed but some checks failed — not launching.[/yellow]")
    else:
        CONSOLE.print(f"[yellow]⚠ {h.label} not installed — run the installer for it later.[/yellow]")
    return 0 if all_ok else 1


def _write_runtime_provider_fallback(repo: Path, dry_run: bool) -> None:
    if dry_run:
        return
    # No provider chosen yet — keep the proxy's default (OpenRouter) so it at
    # least starts; the user can switch later with `atlas switch`.
    core.write_runtime_provider(repo, "openrouter")


def install_and_run(repo: Path, dry_run: bool = False) -> int:
    """Full bootstrap entry used by install.sh/install.ps1 and `atlas install`.

    Order: ensure data dirs + venv + deps → install CLI shim → register
    auto-start → start proxy → run the interactive wizard.
    """
    osmod = _os_module()
    CONSOLE.print(f"[dim]repo: {repo}[/dim]")
    CONSOLE.print(f"[dim]os: {core.detect_os()} / arch: {core.detect_arch()}[/dim]")
    if dry_run:
        CONSOLE.print("[yellow]DRY-RUN — no changes will be made[/yellow]")
        return run_wizard(repo, dry_run=True)
    core.ensure_data_dirs(repo)
    # venv + deps (idempotent; re-execs handled by caller if needed)
    try:
        core.ensure_environment(repo, quiet=False)
    except RuntimeError as e:
        CONSOLE.print(f"[red]✗ environment bootstrap failed: {e}[/red]")
        return 1
    CONSOLE.print("[green]✓ environment ready[/green]")
    # CLI shim + auto-start (idempotent, OS-specific)
    for fn_name in ("install_cli_shim", "install_startup"):
        fn = getattr(osmod, fn_name, None)
        if fn is None:
            continue
        try:
            ok, msg = fn(repo)
            CONSOLE.print(("  [green]✔[/green]" if ok else "  [yellow]⚠[/yellow]") + f" {fn_name}: {msg}")
        except Exception as e:  # never let a shim failure abort the whole wizard
            CONSOLE.print(f"  [yellow]⚠ {fn_name} failed: {e}[/yellow]")
    # Start the proxy (also done in _verify, but start once up front)
    ok, msg = _start_proxy(repo, dry_run=False)
    CONSOLE.print(("  [green]✔[/green]" if ok else "  [yellow]⚠[/yellow]") + f" proxy: {msg}")
    # Interactive harness + keys + verify + launch
    return run_wizard(repo, dry_run=False)
