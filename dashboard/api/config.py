"""Dashboard configuration — env-overridable, independent of the proxy config.

Reuses the proxy's on-disk state (key file, runtime_provider.json) via
services, but keeps its own listener/paths so the two processes are decoupled.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

APP_HOST = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
APP_PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))

# Public TLS listener (reverse proxy / TLS termination target).
PUBLIC_HOST = os.environ.get("DASHBOARD_PUBLIC_HOST", "0.0.0.0")
PUBLIC_PORT = int(os.environ.get("DASHBOARD_PUBLIC_PORT", "8080"))
PUBLIC_TLS = os.environ.get("DASHBOARD_PUBLIC_TLS", "0") in ("1", "true", "yes")
TLS_CERT = os.environ.get("DASHBOARD_TLS_CERT", "")
TLS_KEY = os.environ.get("DASHBOARD_TLS_KEY", "")

DATA_DIR = Path(os.environ.get("DASHBOARD_DATA_DIR", str(REPO_ROOT / "data" / "dashboard")))
DB_PATH = Path(os.environ.get("DASHBOARD_DB", str(DATA_DIR / "atlas.db")))

# Proxy base — the admin API reads live state from the running proxy over HTTP.
PROXY_BASE = os.environ.get("ATLAS_PROXY_URL", "http://127.0.0.1:8788")

# Session policy
SESSION_COOKIE = "atlas_session"
SESSION_IDLE_SECONDS = int(os.environ.get("DASHBOARD_SESSION_IDLE", "1800"))       # 30 min
SESSION_ABSOLUTE_SECONDS = int(os.environ.get("DASHBOARD_SESSION_ABS", "43200"))   # 12 h

# Setup token
SETUP_TOKEN_TTL_SECONDS = int(os.environ.get("DASHBOARD_SETUP_TTL", "900"))       # 15 min

# Login rate limit
LOGIN_MAX_ATTEMPTS = int(os.environ.get("DASHBOARD_LOGIN_MAX", "5"))
LOGIN_WINDOW_SECONDS = int(os.environ.get("DASHBOARD_LOGIN_WINDOW", "300"))

# Frontend dist (served when built) — enables single-process prod deploy.
WEB_DIST = Path(os.environ.get("DASHBOARD_WEB_DIST", str(REPO_ROOT / "dashboard" / "web" / "dist")))

# --- Atlas installation paths (read-only integration points) ----------------
ATLAS_ROOT = REPO_ROOT
PROXY_LOG = Path(os.environ.get("ATLAS_PROXY_LOG", str(REPO_ROOT / "proxy" / "logs" / "atlas-proxy.log")))
PROXY_LOG_DIR = PROXY_LOG.parent
RUNTIME_LOG = Path(os.environ.get("ATLAS_RUNTIME_LOG", str(REPO_ROOT / "data" / "run.log")))
ENV_FILE = Path(os.environ.get("ATLAS_ENV_FILE", str(REPO_ROOT / ".env")))
ENV_EXAMPLE = REPO_ROOT / "proxy" / ".env.example"
BACKUP_DIR = DATA_DIR / "backups"
VERSION = "0.2.0"

# Trusted hosts (Host header allowlist). Empty = allow all hosts; set
# DASHBOARD_ALLOWED_HOSTS to a comma-separated list to restrict in production.
_ALLOWED_HOSTS_RAW = os.environ.get("DASHBOARD_ALLOWED_HOSTS", "")
ALLOWED_HOSTS = [h.strip() for h in _ALLOWED_HOSTS_RAW.split(",") if h.strip()]


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
