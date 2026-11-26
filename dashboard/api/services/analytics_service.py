"""Analytics service — real samples only, persisted in metric_samples.

Nothing here invents a number. Every figure is either read from the running
proxy, parsed from the proxy's log, or sampled from those two over time.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List

from .. import db
from . import key_service, log_service, proxy_client

_SAMPLE_INTERVAL = 30          # seconds between samples
_MAX_AGE = 60 * 24 * 3600      # keep 60 days


def sample_now() -> Dict[str, Any]:
    """Take one sample from live sources and persist it."""
    live = proxy_client.live_summary()
    keys = live.get("keys", {})
    detail = (proxy_client.key_health() or {}).get("keys", []) or []
    lat = [d.get("last_latency_ms") for d in detail if d.get("last_latency_ms")]
    total_req = sum(d.get("total_requests", 0) for d in detail)
    total_err = sum(d.get("total_errors", 0) for d in detail)
    tail = log_service.TAIL.summary()

    row = {
        "active_reqs": keys.get("in_flight", 0),
        "total_keys": keys.get("total", 0),
        "healthy_keys": keys.get("healthy", 0),
        "avg_latency_ms": round(sum(lat) / len(lat), 1) if lat else None,
        "error_rate": round((total_err / total_req) * 100, 2) if total_req else 0.0,
        "throughput": tail.get("avg_latency_ms"),
    }
    ts = db.now()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO metric_samples (ts, active_reqs, total_keys, healthy_keys,"
            " avg_latency_ms, error_rate, throughput) VALUES (?,?,?,?,?,?,?)",
            (ts, row["active_reqs"], row["total_keys"], row["healthy_keys"],
             row["avg_latency_ms"], row["error_rate"], row["throughput"]),
        )
        conn.execute("DELETE FROM metric_samples WHERE ts < ?", (ts - _MAX_AGE,))
    return row


def series(hours: int = 6, bucket: int = 300) -> List[Dict[str, Any]]:
    """Time-bucketed series for charts. Real rows only."""
    since = db.now() - hours * 3600
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT ts, active_reqs, total_keys, healthy_keys, avg_latency_ms,"
            " error_rate FROM metric_samples WHERE ts >= ? ORDER BY ts", (since,)
        ).fetchall()
    out: List[Dict[str, Any]] = []
    cur_bucket = None
    acc: Dict[str, Any] = {}
    n = 0
    for r in rows:
        b = r["ts"] // bucket
        if b != cur_bucket:
            if cur_bucket is not None and n:
                out.append({k: (round(v / n, 2) if isinstance(v, float) else v)
                            for k, v in acc.items()})
            cur_bucket = b
            acc = {"ts": b * bucket, "active_reqs": 0, "healthy_keys": 0,
                   "avg_latency_ms": 0.0, "error_rate": 0.0, "samples": 0}
            n = 0
        acc["active_reqs"] += r["active_reqs"] or 0
        acc["healthy_keys"] += r["healthy_keys"] or 0
        acc["avg_latency_ms"] += r["avg_latency_ms"] or 0.0
        acc["error_rate"] += r["error_rate"] or 0.0
        acc["samples"] += 1
        n += 1
    if cur_bucket is not None and n:
        last = {k: (round(v / n, 2) if isinstance(v, float) else v)
                for k, v in acc.items()}
        last["ts"] = cur_bucket * bucket
        out.append(last)
    return out


def model_usage(limit: int = 20) -> List[Dict[str, Any]]:
    """Model/provider/error breakdown from the assembled request records."""
    counts: Dict[str, Dict[str, Any]] = {}
    for r in log_service.TAIL.requests(limit=2000):
        model = r.get("model") or "unknown"
        e = counts.setdefault(model, {"model": model, "requests": 0, "errors": 0,
                                      "provider": r.get("provider") or ""})
        e["requests"] += 1
        if r.get("state") == "failed":
            e["errors"] += 1
    out = sorted(counts.values(), key=lambda x: -x["requests"])[:limit]
    for e in out:
        e["error_rate"] = round(e["errors"] / e["requests"] * 100, 1) if e["requests"] else 0.0
    return out


def key_utilisation(limit: int = 15) -> List[Dict[str, Any]]:
    """Per-key request counts joined to live health, masked."""
    live = {d["index"]: d for d in (proxy_client.key_health() or {}).get("keys", [])}
    counts: Dict[int, int] = {}
    for r in log_service.TAIL.requests(limit=2000):
        ki = r.get("key_index")
        if ki is not None:
            counts[ki] = counts.get(ki, 0) + 1
    masked = {k["index"]: k["masked"] for k in key_service.list_keys()}
    out = []
    for idx, n in sorted(counts.items(), key=lambda x: -x[1])[:limit]:
        d = live.get(idx, {})
        out.append({
            "index": idx,
            "masked": masked.get(idx, f"#{idx}"),
            "requests": n,
            "errors": d.get("total_errors", 0),
            "state": d.get("state", "UNKNOWN"),
            "last_latency_ms": d.get("last_latency_ms"),
        })
    return out


class Sampler(threading.Thread):
    """Background sampler — the only writer of metric_samples."""

    daemon = True

    def __init__(self) -> None:
        super().__init__(name="atlas-analytics", daemon=True)
        self._stop = threading.Event()

    def run(self) -> None:
        # Sample immediately so the first chart has a point, then on interval.
        try:
            log_service.poll()
            sample_now()
        except Exception:  # noqa: BLE001
            pass
        while not self._stop.wait(_SAMPLE_INTERVAL):
            try:
                log_service.poll()
                sample_now()
            except Exception:  # noqa: BLE001 — a sampler must never die
                pass

    def stop(self) -> None:
        self._stop.set()


_SAMPLER: Sampler | None = None


def start_sampler() -> None:
    global _SAMPLER
    if _SAMPLER is None:
        _SAMPLER = Sampler()
        _SAMPLER.start()


def stop_sampler() -> None:
    global _SAMPLER
    if _SAMPLER is not None:
        _SAMPLER.stop()
        _SAMPLER = None


def overview() -> Dict[str, Any]:
    return {
        "series": series(hours=6),
        "models": model_usage(),
        "keys": key_utilisation(),
        "samples": len(series(hours=24)),
        "interval_seconds": _SAMPLE_INTERVAL,
    }