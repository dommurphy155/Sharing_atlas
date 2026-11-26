"""Proxy client — live state from the running proxy over HTTP.

Dashboard is independent: if the proxy is down this returns a clean
'unreachable' state rather than raising.
"""
from __future__ import annotations

import httpx

from ..config import PROXY_BASE


def _get(path: str, timeout: float = 3.0):
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(f"{PROXY_BASE}{path}")
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


def health() -> dict | None:
    return _get("/health")


def key_health() -> dict | None:
    return _get("/health/keys")


def stats() -> dict | None:
    return _get("/stats")


def root_info() -> dict | None:
    return _get("/")


def live_summary() -> dict:
    """Aggregated status for the overview — never fabricates numbers.

    Deliberately omits per-key detail: the overview polls every few seconds and
    1000+ key records per poll is wasted bandwidth. /health/keys serves detail.
    """
    h = health()
    kh = key_health()
    root = root_info()
    reachable = h is not None
    keys_summary = (kh or {}).get("summary") or (h or {}).get("keys") or {}
    return {
        "reachable": reachable,
        "proxy": {
            "status": (h or {}).get("status", "unreachable"),
            "listen": (h or {}).get("listen", ""),
        },
        "keys": {
            "total": keys_summary.get("total", 0),
            "healthy": keys_summary.get("healthy", 0),
            "cooling": keys_summary.get("cooling", 0),
            "suspended": keys_summary.get("suspended", 0),
            "in_flight": keys_summary.get("in_flight", 0),
        },
        "service": (root or {}).get("service", "Atlas Proxy"),
        "version": (root or {}).get("version", ""),
        "provider": (root or {}).get("provider", ""),
        "default_model": (root or {}).get("default_model", ""),
    }
