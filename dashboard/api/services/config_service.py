"""Config service — reads proxy.config, writes runtime_provider.json like the CLI.

Also owns the `.env` surface: parse, validate, back up, write. The proxy reads
`.env` through systemd's EnvironmentFile, so a write here changes the next
proxy start — never the running process. That is stated in the API response.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List

from ..config import ENV_EXAMPLE, ENV_FILE, REPO_ROOT

_RUNTIME_PROVIDER = REPO_ROOT / "data" / "proxy_data" / "runtime_provider.json"

# Env keys the dashboard will read and write. Anything else in .env is
# preserved verbatim on write.
MANAGED_KEYS = [
    "LISTEN_HOST", "LISTEN_PORT", "ATLAS_OPENROUTER_MODEL",
    "ATLAS_NV_MODEL", "FORCE_DEFAULT_MODEL", "MAX_RETRIES",
    "MAX_CONCURRENT_PER_KEY", "COOLDOWN_BASE_SECONDS", "SUSPEND_SECONDS",
    "MODEL_CONTEXT_WINDOW", "MAX_INPUT_TOKENS", "LOG_LEVEL", "LOG_JSON",
]

_INT_KEYS = {"LISTEN_PORT", "MAX_RETRIES", "MAX_CONCURRENT_PER_KEY",
             "MODEL_CONTEXT_WINDOW", "MAX_INPUT_TOKENS"}
_FLOAT_KEYS = {"COOLDOWN_BASE_SECONDS", "SUSPEND_SECONDS"}
_BOOL_KEYS = {"FORCE_DEFAULT_MODEL", "LOG_JSON"}

# A .env key: letters, digits, underscore; must not start with a digit.
_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _proxy_config():
    """Import proxy.config from the repo root (same source the CLI uses)."""
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    import proxy.config as cfg  # noqa: WPS433
    return cfg


def resolved() -> Dict[str, Any]:
    cfg = _proxy_config()
    return {
        "provider": cfg.PROVIDER,
        "default_model": cfg.get_default_model(),
        "listen": f"{cfg.LISTEN_HOST}:{cfg.LISTEN_PORT}",
        "key_file": str(cfg.KEY_FILE),
        "runtime_provider_file": str(cfg.RUNTIME_PROVIDER_FILE),
        "context_window": cfg.MODEL_CONTEXT_WINDOW,
        "force_default_model": cfg.get_force_default_model(),
        "max_input_tokens": cfg.MAX_INPUT_TOKENS,
        "max_retries": cfg.MAX_RETRIES,
        "max_concurrent_per_key": cfg.MAX_CONCURRENT_PER_KEY,
        "cooldown_base_seconds": cfg.COOLDOWN_BASE_SECONDS,
        "suspend_seconds": cfg.SUSPEND_SECONDS,
    }


def providers() -> List[str]:
    return ["openrouter", "nvidia"]


def read_runtime_provider() -> Dict[str, Any]:
    try:
        return json.loads(_RUNTIME_PROVIDER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"provider": "openrouter", "model": ""}


def write_runtime_provider(provider: str, model: str = "") -> Dict[str, Any]:
    """Write runtime_provider.json exactly as `atlas switch` does.

    Merges into the existing object rather than replacing it, so fields the CLI
    owns (`selected_at`) survive a dashboard write and vice versa.
    """
    _RUNTIME_PROVIDER.parent.mkdir(parents=True, exist_ok=True)
    existing: Dict[str, Any] = {}
    if _RUNTIME_PROVIDER.exists():
        try:
            existing = json.loads(_RUNTIME_PROVIDER.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
    existing.update({
        "provider": provider,
        "model": model,
        "selected_at": int(time.time()),
    })
    _RUNTIME_PROVIDER.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return existing


# ---------------------------------------------------------------------------
# .env surface
# ---------------------------------------------------------------------------
_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def parse_env(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _LINE.match(line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def read_env() -> Dict[str, Any]:
    """Env values with the resolved default beside each, secrets masked."""
    raw = {}
    if ENV_FILE.exists():
        raw = parse_env(ENV_FILE.read_text(encoding="utf-8", errors="replace"))
    cfg = _proxy_config()
    defaults = {
        "LISTEN_HOST": "127.0.0.1", "LISTEN_PORT": "8788",
        "MAX_RETRIES": "5", "MAX_CONCURRENT_PER_KEY": "24",
        "COOLDOWN_BASE_SECONDS": "45.0", "SUSPEND_SECONDS": "600.0",
        "MODEL_CONTEXT_WINDOW": "200000", "MAX_INPUT_TOKENS": "180000",
        "LOG_LEVEL": "INFO", "LOG_JSON": "0", "FORCE_DEFAULT_MODEL": "0",
        "ATLAS_OPENROUTER_MODEL": cfg.get_default_model(),
        "ATLAS_NV_MODEL": "",
    }
    items = []
    for k in MANAGED_KEYS:
        v = raw.get(k)
        items.append({
            "key": k,
            "value": v,
            "default": defaults.get(k, ""),
            "effective": v if v not in (None, "") else defaults.get(k, ""),
            "overridden": v not in (None, ""),
            "kind": ("int" if k in _INT_KEYS else
                     "float" if k in _FLOAT_KEYS else
                     "bool" if k in _BOOL_KEYS else "str"),
        })
    return {"file": str(ENV_FILE), "exists": ENV_FILE.exists(), "items": items}


def validate_env(values: Dict[str, str]) -> List[str]:
    """Return human-readable problems. Empty list = valid.

    Rejects anything that could break out of a single ``KEY=value`` line.
    ``.env`` is consumed by systemd as an EnvironmentFile, so a newline in a
    value injects an entirely new directive (e.g. LD_PRELOAD) that the proxy
    would inherit on its next start.
    """
    errs: List[str] = []
    for k, v in values.items():
        if not _KEY_RE.fullmatch(k or ""):
            errs.append(f"Invalid key {k!r}.")
            continue
        if k not in MANAGED_KEYS:
            # Unmanaged keys are preserved, not written from user input.
            continue
        v = (v or "").strip()
        if v == "":
            continue
        if "\n" in v or "\r" in v or "\x00" in v:
            errs.append(f"{k} must not contain newlines or null bytes.")
            continue
        if k in _INT_KEYS:
            try:
                n = int(v)
            except ValueError:
                errs.append(f"{k} must be an integer (got {v!r}).")
                continue
            if n <= 0:
                errs.append(f"{k} must be greater than 0.")
        elif k in _FLOAT_KEYS:
            try:
                f = float(v)
            except ValueError:
                errs.append(f"{k} must be a number (got {v!r}).")
                continue
            if f < 0:
                errs.append(f"{k} must not be negative.")
        elif k in _BOOL_KEYS:
            if v.lower() not in ("0", "1", "true", "false", "yes", "no", "on", "off"):
                errs.append(f"{k} must be a boolean (0/1/true/false).")
        if k == "LISTEN_PORT":
            try:
                if not (1 <= int(v) <= 65535):
                    errs.append("LISTEN_PORT must be between 1 and 65535.")
            except ValueError:
                pass
        if k == "LISTEN_HOST" and v and not re.fullmatch(r"[A-Za-z0-9\.\-:]+", v):
            errs.append("LISTEN_HOST contains characters that are not valid in a host.")
    return errs


def preview_env(values: Dict[str, str]) -> Dict[str, Any]:
    current = read_env()
    have = {i["key"]: (i["value"] or "") for i in current["items"]}
    changes = []
    for k, v in values.items():
        new = (v or "").strip()
        old = have.get(k, "")
        if new != old:
            changes.append({"key": k, "from": old, "to": new})
    return {"errors": validate_env(values), "changes": changes,
            "count": len(changes)}


def write_env(values: Dict[str, str], backup: bool = True) -> Dict[str, Any]:
    """Rewrite .env, preserving comments and unmanaged keys verbatim.

    Only managed keys are ever written from user input; anything else in the
    request is dropped. This is the defence-in-depth counterpart to the
    newline check in validate_env: even if a future caller forgets to
    validate, an attacker cannot introduce a new directive.
    """
    errs = validate_env(values)
    if errs:
        return {"ok": False, "errors": errs}
    safe = {k: v for k, v in values.items() if k in MANAGED_KEYS}
    backup_path = None
    if backup and ENV_FILE.exists():
        from . import system_service
        backup_path = system_service.backup_config("env")["backup"]

    lines: List[str] = []
    seen = set()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _LINE.match(line)
            if m and m.group(1) in safe:
                k = m.group(1)
                seen.add(k)
                lines.append(f"{k}={safe[k]}")
            else:
                lines.append(line)
    for k, v in safe.items():
        if k not in seen:
            lines.append(f"{k}={v}")
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)
    return {"ok": True, "backup": backup_path,
            "note": "Applied on next proxy restart."}


def example_keys() -> List[str]:
    if not ENV_EXAMPLE.exists():
        return []
    return [k for k in parse_env(ENV_EXAMPLE.read_text(errors="replace")) if k]
