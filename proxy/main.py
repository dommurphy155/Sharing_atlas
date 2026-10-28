"""FastAPI app, lifespan, and uvicorn entrypoint.

Run:
  python -m proxy.main
"""

from __future__ import annotations

import sys
import os
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import routes
from .config import (
    CORS_ORIGINS,
    FALLBACK_KEY_FILE,
    KEY_FILE,
    KEEPALIVE_EXPIRY,
    LISTEN_HOST,
    LISTEN_PORT,
    LOG_LEVEL,
    PROVIDER,
    NV_KEY_FILE,
    SYSTEM_PROMPT_OVERRIDE_FILE,
    get_default_model,
    get_force_default_model,
    get_key_file,
    get_key_prefix,
    reload_system_prompt_override,
    log,
)
from .keypool import KeyPool, load_keys
from .proxy import ProxyCore
from . import prettylog as _prettylog

# Logging-only: swap the pretty formatter onto the existing root handler(s),
# then mirror every line into proxy/logs/atlas-proxy.log (plain text, rotated).
_prettylog.attach()
_prettylog.mirror_file()

# Optional high-performance event loop
try:
    import uvloop

    uvloop.install()
except ImportError:
    pass


def _load_provider_keys() -> list[str]:
    """Load keys from the provider-appropriate file.

    Returns keys from the active provider's key file. OpenRouter uses
    KEY_FILE, NVIDIA uses NV_KEY_FILE.
    """
    if PROVIDER == "nvidia":
        return load_keys(NV_KEY_FILE)
    return load_keys(KEY_FILE)


# Public alias retained for the test-suite / external callers.
_load_active_keys = _load_provider_keys


def _reload_keys_for_provider() -> list[str]:
    """Reload keys for the active provider.

    OpenRouter falls back to FALLBACK_KEY_FILE when the primary is empty.
    """
    if PROVIDER == "nvidia":
        return load_keys(NV_KEY_FILE)
    # OpenRouter: use fallback if primary is empty (existing behaviour)
    keys = load_keys(KEY_FILE)
    if not keys:
        keys = load_keys(FALLBACK_KEY_FILE)
        if keys:
            log.warning(
                "Primary keys file missing – using fallback %s (%d keys)",
                FALLBACK_KEY_FILE,
                len(keys),
            )
    return keys


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Shuffle key file in-place before pool construction (startup only) ---
    # Randomises key order so fresh boots don't hammer the same keys in
    # sequence after repeated local testing/restarts.
    # Pure-Python (replaces `shuf`) so it works on macOS too.
    _key_file = KEY_FILE
    try:
        import random
        with open(_key_file) as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        random.shuffle(lines)
        tmp = Path(str(_key_file) + ".tmp")
        with open(tmp, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, _key_file)
    except FileNotFoundError:
        pass  # no key file yet — pool starts empty and hot-reloads
    except Exception as e:
        log.warning("key-file shuffle failed (non-fatal): %s", e)

    keys = _reload_keys_for_provider()

    if not keys:
        log.warning(
            "No keys found yet. Expected file: %s (one %s key per line). "
            "Proxy will start and auto-load keys as they become available.",
            get_key_file(),
            get_key_prefix(),
        )
        keys = []  # Start with empty pool, will be populated

    pool_mode = "partial_sticky"
    log.info("Loaded %d %s keys (mode=%s)", len(keys), PROVIDER, pool_mode)
    pool = KeyPool(keys, mode=pool_mode) if keys else KeyPool([], mode=pool_mode)

    # For OpenRouter, preserve existing fallback behavior
    if PROVIDER == "openrouter" and not keys:
        fallback_keys = load_keys(FALLBACK_KEY_FILE)
        if fallback_keys:
            log.warning(
                "Primary keys file missing – using fallback %s (%d keys)",
                FALLBACK_KEY_FILE,
                len(fallback_keys),
            )
            pool = KeyPool(fallback_keys)

    core = ProxyCore(pool, provider=PROVIDER)

    # Start background key reloader
    reload_task = asyncio.create_task(_reload_keys_periodically(pool))

    await core.start()
    routes.proxy = core
    log.info(
        "Proxy listening on http://%s:%d  (keys=%d, healthy=%d, "
        "provider=%s, default_model=%s, force=%s)",
        LISTEN_HOST,
        LISTEN_PORT,
        pool.stats()["total"],
        pool.stats()["healthy"],
        PROVIDER,
        get_default_model(),
        get_force_default_model(),
    )
    yield
    reload_task.cancel()
    await core.stop()
    routes.proxy = None
    log.info("Shutdown complete")


async def _reload_keys_periodically(pool: KeyPool) -> None:
    """Periodically reload keys from file and update pool.

    Uses the active provider's key file (see _load_provider_keys).
    Also hot-reloads system prompt override (shared).

    Change detection is content-based, not mtime-based: we compare the
    parsed key list against the pool's current keys.  This means rapid
    successive writes, preserved mtimes, or partial writes that happen
    to share a timestamp cannot leave the pool stale.
    """
    last_override_mtime = 0
    last_key_fingerprint: Optional[tuple] = None  # (file_path, tuple_of_keys)

    while True:
        try:
            await asyncio.sleep(5)  # Check every 5 seconds

            override_file = Path(SYSTEM_PROMPT_OVERRIDE_FILE)
            if override_file.exists():
                omtime = override_file.stat().st_mtime
                if omtime > last_override_mtime:
                    last_override_mtime = omtime
                    reload_system_prompt_override()

            # Provider-specific key file
            key_file_path = get_key_file()

            key_file = Path(key_file_path)
            if not key_file.exists():
                continue

            new_keys = _load_provider_keys()
            fingerprint = (key_file_path, tuple(new_keys))

            # Skip if nothing changed (content-based, not mtime)
            if fingerprint == last_key_fingerprint:
                continue
            last_key_fingerprint = fingerprint

            current_key_strs = [k.key for k in pool._keys]
            if new_keys and new_keys != current_key_strs:
                old_count = len(pool._keys)
                added, removed, kept = await pool.reload_keys(new_keys)
                log.info(
                    "Reloaded keys from %s (%d → %d keys, +%d/-%d/=%d kept)",
                    key_file_path,
                    old_count,
                    len(pool._keys),
                    added,
                    removed,
                    kept,
                )
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("Error reloading keys: %s", e)


app = FastAPI(
    title="OpenRouter Translation Proxy",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-request-id"],
)

app.include_router(routes.router)


def main() -> None:
    import uvicorn

    # Pass the app object, not "proxy.main:app" — the string form makes uvicorn
    # re-import this module while __main__ already ran it, double-executing all
    # module-level setup (incl. logging handlers).
    uvicorn.run(
        app,
        host=LISTEN_HOST,
        port=LISTEN_PORT,
        log_level=LOG_LEVEL.lower(),
        loop="uvloop" if "uvloop" in sys.modules else "asyncio",
        http="httptools",
        timeout_keep_alive=int(KEEPALIVE_EXPIRY),
        access_log=False,
    )


if __name__ == "__main__":
    main()
