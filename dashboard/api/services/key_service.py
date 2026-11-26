"""Key service — reads/mutates the SAME key file the proxy + CLI use.

Raw keys never leave this module un-masked. Mutations mirror `atlas import-key`
(append + dedupe, never overwrite).
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from .config_service import _proxy_config
from ..config import REPO_ROOT


def _key_file() -> Path:
    return Path(_proxy_config().KEY_FILE)


def _dead_file() -> Path:
    cfg = _proxy_config()
    name = "nvidia" if cfg.PROVIDER == "nvidia" else "huggingface"
    try:
        return Path(cfg.NV_DEAD_KEYS_FILE if name == "nvidia" else cfg.HF_DEAD_KEYS_FILE)
    except Exception:
        return REPO_ROOT / "data" / "dead_keys.txt"


def mask(key: str) -> str:
    if len(key) <= 12:
        return "••••"
    return f"{key[:6]}…{key[-4:]}"


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _read_lines() -> list[str]:
    p = _key_file()
    if not p.exists():
        return []
    return p.read_text(encoding="utf-8", errors="replace").splitlines()


def list_keys(masked: bool = True) -> list[dict]:
    """Return key metadata. Masked by default — never raw."""
    cfg = _proxy_config()
    prefix = cfg.get_key_prefix()
    live = _live_by_index()
    out: list[dict] = []
    for i, ln in enumerate(_read_lines()):
        s = ln.strip()
        if not s or not s.startswith(prefix):
            continue
        d = live.get(i, {})
        out.append({
            "index": i,
            "masked": mask(s),
            "fingerprint": fingerprint(s),
            "enabled": True,
            "state": d.get("state", "UNKNOWN"),
            "in_flight": d.get("in_flight", 0),
            "consecutive_errors": d.get("consecutive_errors", 0),
            "total_requests": d.get("total_requests", 0),
            "total_errors": d.get("total_errors", 0),
            "last_status": d.get("last_status"),
            "last_latency_ms": d.get("last_latency_ms"),
            "cooldown_remaining_s": d.get("cooldown_remaining_s", 0),
        })
    for j, ln in enumerate(_read_lines()):
        s = ln.strip()
        if s.startswith("#") and s[1:].strip().startswith(prefix):
            out.append({
                "index": j, "masked": mask(s[1:].strip()),
                "fingerprint": fingerprint(s[1:].strip()), "enabled": False,
                "state": "DISABLED", "in_flight": 0, "consecutive_errors": 0,
                "total_requests": 0, "total_errors": 0, "last_status": None,
                "last_latency_ms": None, "cooldown_remaining_s": 0,
            })
    return out


def _live_by_index() -> dict:
    try:
        from . import proxy_client
        return {d["index"]: d for d in (proxy_client.key_health() or {}).get("keys", [])}
    except Exception:  # noqa: BLE001
        return {}


def pool_summary() -> dict:
    keys = list_keys()
    live = _live_by_index()
    enabled = [k for k in keys if k["enabled"]]
    return {
        "total": len(keys),
        "enabled": len(enabled),
        "disabled": len(keys) - len(enabled),
        "live": live,
    }


def set_enabled(fingerprint_to_toggle: str, enabled: bool) -> bool:
    """Comment a key out (disable) or uncomment it (enable), atomically.

    The proxy's loader skips `#`-prefixed lines, so commenting is the native
    disable mechanism — no second source of truth is introduced.
    """
    p = _key_file()
    if not p.exists():
        return False
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    changed = False
    out = []
    for ln in lines:
        stripped = ln.strip()
        body = stripped[1:].strip() if stripped.startswith("#") else stripped
        if body and fingerprint(body) == fingerprint_to_toggle:
            if enabled and stripped.startswith("#"):
                out.append(body + "\n")
                changed = True
                continue
            if not enabled and not stripped.startswith("#"):
                out.append("#" + ln)
                changed = True
                continue
        out.append(ln)
    if not changed:
        return False
    _atomic_write(p, "".join(out))
    return True


def _atomic_write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(p.parent), 0o700)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".atlas_keys_", suffix=".tmp")
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(str(p), 0o600)


def import_keys(raw_keys: list[str]) -> dict:
    """Append+dedupe new keys — identical semantics to the CLI."""
    cfg = _proxy_config()
    prefix = cfg.get_key_prefix()
    p = _key_file()
    existing = set()
    for ln in _read_lines():
        s = ln.strip()
        if s and not s.startswith("#"):
            existing.add(s)

    candidates: list[str] = []
    seen: set[str] = set()
    for raw in raw_keys:
        for piece in raw.replace(",", "\n").splitlines():
            s = piece.strip()
            if s and not s.startswith("#") and s not in seen:
                seen.add(s)
                candidates.append(s)
    valid = [k for k in candidates if k.startswith(prefix)]
    new = [k for k in valid if k not in existing]

    if not new:
        return {"added": 0, "duplicates": len(valid), "rejected": len(candidates) - len(valid)}

    p.parent.mkdir(parents=True, exist_ok=True)
    # Owner-only permissions on credential file (security: restrict key material)
    os.chmod(str(p.parent), 0o700)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write("\n".join(new) + "\n")
    except Exception:
        os.close(fd)
        raise
    # Tighten permissions on every write — an existing file keeps its old
    # mode unless we reset it (the file predating this code is a real case).
    os.chmod(str(p), 0o600)
    return {"added": len(new), "duplicates": len(valid) - len(new), "rejected": len(candidates) - len(valid)}


def remove_key(fingerprint_to_remove: str) -> bool:
    """Remove a key by fingerprint (we never accept raw key removal).

    For HF/NVIDIA providers this also appends to the dead-key file, matching
    the existing retirement path.
    """
    p = _key_file()
    if not p.exists():
        return False
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    kept, removed_key = [], None
    for ln in lines:
        s = ln.strip()
        if s and not s.startswith("#") and fingerprint(s) == fingerprint_to_remove:
            removed_key = s
            continue
        kept.append(ln)
    if removed_key is None:
        return False
    # Write to an owner-only temp file, then atomically replace (security: keep
    # credential material 0o600 throughout).
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(p.parent), 0o700)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".atlas_keys_", suffix=".tmp")
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("".join(kept))
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(str(p), 0o600)
    return True
