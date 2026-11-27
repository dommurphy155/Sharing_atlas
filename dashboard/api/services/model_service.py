"""Model catalogue service — the dashboard's view of `atlas switch`.

Reuses atlas/bin/models.py verbatim (same public catalogue fetchers, same
6-hour cache, same ranking) and atlas/bin/switch.py's save path. No second
model list, no duplicated parsing: the dashboard and the CLI read the same
cache file and write the same runtime_provider.json.

The one thing this adds is a non-interactive switch: pick provider + model in
the browser and the service writes the file and restarts the proxy through the
runtime layer, exactly as `atlas switch` does.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from ..config import ATLAS_ROOT


def _models_mod():
    """Import atlas/bin/models.py — the CLI's own catalogue layer."""
    atlas_dir = str(ATLAS_ROOT / "atlas")
    if atlas_dir not in sys.path:
        sys.path.insert(0, atlas_dir)
    from bin import models as m  # noqa: WPS433
    return m


def providers() -> List[Dict[str, str]]:
    m = _models_mod()
    return [{"key": p.key, "label": p.label, "description": p.description}
            for p in m.PROVIDER_MENU]


def _serialise(model) -> Dict[str, Any]:
    return {
        "id": model.id,
        "name": model.name,
        "provider": model.provider,
        "context_length": model.context_length,
        "context": _ctx(model.context_length),
        "tasks": [t for t in model.tasks if t != "conversational"] or ["chat"],
        "prompt_price": model.prompt_price,
        "completion_price": model.completion_price,
        "is_free": model.is_free,
        "score_hint": round(model.score_hint, 3),
    }


def _ctx(n: int) -> str:
    if n <= 0:
        return "—"
    if n >= 1_000_000:
        return f"{n // 1_000_000}M"
    if n >= 1000:
        return f"{n // 1000}k"
    return str(n)


def catalogue(provider: str, refresh: bool = False,
              free_only: bool = True) -> Dict[str, Any]:
    """Models for a provider, from the CLI's cache (or refetched on request).

    Never raises: a catalogue outage must not take the page down, so the error
    travels back as data and the UI shows it.
    """
    m = _models_mod()
    slug = provider.lower()
    if slug not in {p.key for p in m.PROVIDER_MENU}:
        return {"ok": False, "error": f"Unknown provider: {provider}",
                "models": [], "provider": slug}

    ttl = 0 if refresh else m.CACHE_TTL_SECONDS
    try:
        if slug == "openrouter":
            models = asyncio.run(m.openrouter_models(ATLAS_ROOT, ttl=ttl,
                                                     free_only=free_only))
        elif slug == "nvidia":
            models = asyncio.run(m.nvidia_models(ATLAS_ROOT, ttl=ttl))
        else:
            models = []
    except Exception as exc:  # noqa: BLE001 — surface, never crash
        return {"ok": False, "error": f"Fetch failed: {exc}",
                "models": [], "provider": slug}

    # Same ordering the CLI shows: ranked for coding/reasoning/agentic work.
    try:
        models = m.rank(models)
    except Exception:  # noqa: BLE001
        pass

    items = [_serialise(x) for x in models]
    return {
        "ok": True,
        "provider": slug,
        "count": len(items),
        "models": items,
        "free_only": free_only,
        "source": "cache" if ttl else "live",
    }


def switch(provider: str, model: str) -> Dict[str, Any]:
    """Write runtime_provider.json and restart, as `atlas switch` does.

    The write goes through config_service (the same file, same shape the CLI
    writes) and the restart goes through the runtime layer, so a tmux install
    restarts correctly instead of being systemctl'd.
    """
    from . import config_service, runtime_service
    if provider not in {p["key"] for p in providers()}:
        return {"ok": False, "error": f"Unknown provider: {provider}"}
    payload = config_service.write_runtime_provider(provider, model)
    restart = runtime_service.action("restart")
    return {
        "ok": bool(restart.get("ok")),
        "provider": provider,
        "model": model,
        "written": payload,
        "restart": restart.get("message", ""),
        "note": "runtime_provider.json written; proxy restarted",
    }
