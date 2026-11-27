"""Log service — parses the proxy's own log into request records.

The proxy writes human-readable structured lines. This module is the ONLY
place that turns them into data the dashboard shows. It never returns raw
request/response bodies and redacts anything key-shaped.

Line shapes it understands (from proxy/prettylog.py + routes.py):

    11:57:05  INFO    req=fb34f0 payload_saved /tmp/... (328645 bytes)
    11:57:05  INFO    ▶ fb34f0 OPENAI baidu/ernie-5.5:free STREAM
    11:57:07  INFO    req=fb34f0 key_idx=24 status=200 stream=1
    11:57:07  SUCCESS ✓ fb34f0 200 OPENAI/baidu/ernie-5.5:free
    11:37:55  WARNING req=2b9ecc key_idx=23 upstream rejected status=429 stream=1
    11:38:31  ERROR   ✗ REQ 36edb7
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional

from ..config import PROXY_LOG

# ---------------------------------------------------------------------------
# Redaction — secrets must never reach the browser or this process's memory
# longer than a line parse.
# ---------------------------------------------------------------------------
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"hf_[A-Za-z0-9]{8,}"),
    re.compile(r"nvapi-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(authorization|api[_-]?key|bearer)\s*[:=]\s*\S+"),
)


def redact(text: str) -> str:
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub("«redacted»", out)
    return out


# ---------------------------------------------------------------------------
# Line parsing
# ---------------------------------------------------------------------------
_TS = r"(\d{2}):(\d{2}):(\d{2})"
RE_START = re.compile(rf"^{_TS}\s+\w+\s+▶\s+([0-9a-f]{{4,12}})\s+(\S+)\s+(\S+)(?:\s+(\S+))?")
RE_KEYREQ = re.compile(rf"^{_TS}\s+\w+\s+req=([0-9a-f]{{4,12}})\s+key_idx=(\d+)\s+status=(\d+)")
RE_DONE = re.compile(rf"^{_TS}\s+(\w+)\s+[✓✗]\s+(?:REQ\s+)?([0-9a-f]{{4,12}})\s+(\d+)?\s*(\S*)")
RE_WARNREQ = re.compile(
    rf"^{_TS}\s+\w+\s+req=([0-9a-f]{{4,12}})\s+key_idx=(\d+)\s+(.+)"
)
RE_STATUS = re.compile(r"status=(\d{3})")
RE_ERRBODY = re.compile(rf"^{_TS}\s+\w+\s+req=([0-9a-f]{{4,12}})\s+upstream_error_body\s+status=(\d+)\s+body=(.*)$")
RE_GENERIC = re.compile(rf"^{_TS}\s+(\w+)\s+(.*)$")


def _secs(h: str, m: str, s: str) -> int:
    return int(h) * 3600 + int(m) * 60 + int(s)


def _wallclock(secs: int) -> float:
    """Today's epoch for a HH:MM:SS, rolling back a day if we crossed midnight."""
    now = time.localtime()
    base = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))
    ts = base + secs
    if ts > time.time() + 60:  # log line from "the future" = yesterday
        ts -= 86400
    return ts


class RequestRecord(dict):
    """A single upstream request, assembled from several log lines."""


def parse_line(line: str) -> Optional[Dict[str, Any]]:
    """Return a partial record contribution, or None for uninteresting lines."""
    line = line.rstrip("\n")
    if not line.strip():
        return None

    m = RE_START.match(line)
    if m:
        return {
            "kind": "start",
            "ts": _wallclock(_secs(m.group(1), m.group(2), m.group(3))),
            "id": m.group(4),
            "provider": m.group(5),
            "model": m.group(6),
            "stream": (m.group(7) or "").upper() == "STREAM",
        }

    m = RE_KEYREQ.match(line)
    if m:
        return {
            "kind": "keyreq",
            "ts": _wallclock(_secs(m.group(1), m.group(2), m.group(3))),
            "id": m.group(4),
            "key_index": int(m.group(5)),
            "status": int(m.group(6)),
        }

    m = RE_ERRBODY.match(line)
    if m:
        return {
            "kind": "error",
            "ts": _wallclock(_secs(m.group(1), m.group(2), m.group(3))),
            "id": m.group(4),
            "status": int(m.group(5)),
            "detail": redact(m.group(6))[:400],
        }

    m = RE_WARNREQ.match(line)
    if m:
        status = RE_STATUS.search(m.group(3))
        return {
            "kind": "warn",
            "ts": _wallclock(_secs(m.group(1), m.group(2), m.group(3))),
            "id": m.group(4),
            "key_index": int(m.group(5)),
            "status": int(status.group(1)) if status else None,
            "detail": redact(m.group(3))[:200],
        }

    m = RE_DONE.match(line)
    if m:
        level, rid, status, tail = m.group(1), m.group(2), m.group(3), m.group(4)
        return {
            "kind": "done",
            "ts": _wallclock(_secs(m.group(1), m.group(2), m.group(3))),
            "id": rid,
            "ok": level in ("SUCCESS", "INFO"),
            "status": int(status) if status else None,
            "tail": tail,
        }

    return None


# ---------------------------------------------------------------------------
# In-memory tail: a rolling window assembled from the file
# ---------------------------------------------------------------------------
class LogTail:
    """Reads the proxy log incrementally and keeps assembled request records."""

    MAX_RECORDS = 400
    MAX_RAW = 600

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._fh = None
        self._inode = None
        self._records: Deque[Dict[str, Any]] = deque(maxlen=self.MAX_RECORDS)
        self._by_id: Dict[str, Dict[str, Any]] = {}
        self._raw: Deque[Dict[str, Any]] = deque(maxlen=self.MAX_RAW)
        self._errors: Deque[Dict[str, Any]] = deque(maxlen=200)
        self._offset = 0

    # -- file handling ------------------------------------------------------
    def _open(self) -> None:
        try:
            self._fh = self.path.open("r", encoding="utf-8", errors="replace")
            self._inode = self.path.stat().st_ino
            self._fh.seek(0, 2)
            self._offset = self._fh.tell()
        except OSError:
            self._fh = None
            self._inode = None

    def _reopen_if_rotated(self) -> None:
        try:
            st = self.path.stat()
        except OSError:
            return
        if self._fh is None or self._inode != st.st_ino:
            if self._fh:
                self._fh.close()
            self._open()
            return
        if self._offset > st.st_size:  # truncated
            self._fh.seek(0)
            self._offset = 0

    def _ingest(self, line: str) -> None:
        parsed = parse_line(line)
        if parsed is None:
            m = RE_GENERIC.match(line.rstrip("\n"))
            if m:
                self._raw.append({
                    "ts": _wallclock(_secs(m.group(1), m.group(2), m.group(3))),
                    "level": m.group(4).upper(),
                    "msg": redact(m.group(5))[:300],
                })
            return

        kind = parsed["kind"]
        if kind in ("warn", "error"):
            self._errors.append(parsed)

        rid = parsed.get("id")
        if not rid:
            return
        rec = self._by_id.get(rid)
        if rec is None:
            rec = RequestRecord(
                id=rid, ts=parsed["ts"], provider="", model="", stream=False,
                key_index=None, status=None, ok=None, duration_ms=None,
                detail="", state="open",
            )
            self._by_id[rid] = rec
            self._records.append(rec)

        if kind == "start":
            rec["provider"] = parsed["provider"]
            rec["model"] = parsed["model"]
            rec["stream"] = parsed["stream"]
        elif kind == "keyreq":
            rec["key_index"] = parsed["key_index"]
            rec["status"] = parsed["status"]
        elif kind in ("warn", "error"):
            if parsed.get("status"):
                rec["status"] = parsed["status"]
            if parsed.get("key_index") is not None:
                rec["key_index"] = parsed["key_index"]
            rec["detail"] = parsed.get("detail", "")
            rec["ok"] = False
        elif kind == "done":
            if parsed.get("status"):
                rec["status"] = parsed["status"]
            rec["ok"] = parsed["ok"] and not rec.get("detail")
            rec["state"] = "done" if rec["ok"] else "failed"
            rec["duration_ms"] = max(0, int((parsed["ts"] - rec["ts"]) * 1000))

    def poll(self) -> int:
        """Read any new bytes. Returns lines ingested."""
        with self._lock:
            self._reopen_if_rotated()
            if self._fh is None:
                return 0
            n = 0
            while True:
                line = self._fh.readline()
                if not line:
                    break
                self._offset = self._fh.tell()
                self._ingest(line)
                n += 1
            return n

    def backfill(self, max_lines: int = 4000) -> int:
        """Initial read of the tail of the file so the UI has history."""
        with self._lock:
            try:
                size = self.path.stat().st_size
            except OSError:
                return 0
            with self.path.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(0)
                lines = fh.readlines()[-max_lines:]
            for line in lines:
                self._ingest(line)
            self._open()
            return len(lines)

    # -- readers ------------------------------------------------------------
    def requests(self, limit: int = 100, only_errors: bool = False,
                 query: str = "") -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._records)
        if only_errors:
            items = [r for r in items if r.get("state") == "failed"]
        if query:
            q = query.lower()
            items = [r for r in items if q in r["id"] or q in (r.get("model") or "").lower()]
        items.reverse()
        return [dict(r) for r in items[:limit]]

    def raw_lines(self, limit: int = 200, level: str = "",
                  query: str = "") -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._raw)
        if level:
            items = [r for r in items if r["level"] == level.upper()]
        if query:
            q = query.lower()
            items = [r for r in items if q in r["msg"].lower()]
        return [dict(r) for r in items[-limit:]]

    def recent_errors(self, limit: int = 25) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._errors)
        return [dict(r) for r in items[-limit:]]

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            recs = list(self._records)
        total = len(recs)
        failed = sum(1 for r in recs if r.get("state") == "failed")
        lat = [r["duration_ms"] for r in recs if r.get("duration_ms")]
        return {
            "total": total,
            "failed": failed,
            "avg_latency_ms": round(sum(lat) / len(lat), 1) if lat else None,
            "window": "session",
        }


TAIL = LogTail(PROXY_LOG)


def bootstrap() -> None:
    TAIL.backfill()


def poll() -> None:
    TAIL.poll()
