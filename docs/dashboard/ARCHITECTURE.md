# Atlas Dashboard — Architecture

## Current Atlas (audited)

```
Clients (Claude Code, Codex, Hermes, Cursor, curl)
        │  OpenAI /chat/completions · /v1/messages · /v1/responses
        ▼
proxy/main.py  ── FastAPI app, lifespan builds KeyPool + ProxyCore
  routes.py    ── translate body, forward, self-heal 400s
  translation.py ── bidirectional OpenAI↔Anthropic (tools/images/reasoning), trim, system-prompt inject
  proxy.py     ── key selection, retries, 400-repair, SSE streaming, cleanup
  keypool.py   ── atomic round-robin, health states, cooldown/suspend, sticky, hot-reload
  config.py    ── env-overridable; PROVIDER from data/proxy_data/runtime_provider.json
  prettylog.py ── in-memory RequestTrace ring (last 512), JSON/pretty formats
atlas/bin/atlas ── CLI (argparse) → runtime.py strategy (systemd-user/tmux/nohup/launchd/scheduled-task/manual)
data/            ── runtime_provider.json (provider+model), openrouter_data/*keys.txt, proxy_data/prompt_override.txt, run.log
```

Runtime discovered here: **tmux** session `atlas`, proxy on `0.0.0.0:8788`, 1170 OpenRouter keys.

## Target architecture — two interfaces, one install

```
   ATLAS CLI                         ATLAS DASHBOARD (0.0.0.0:8080 TLS)
      │                                     │
      │                              React + TS + Vite (dashboard/web)
      │                                     │  /api/*
      │                             Dashboard API (dashboard/api)  127.0.0.1:8090
      │                                     │
      └──────────────┬──────────────────────┘
                     │
            SHARED MANAGEMENT LAYER  (dashboard/api/services/*)
                     │  reads/writes the SAME state the CLI does:
                     │  config.py values · data/runtime_provider.json ·
                     │  key file · runtime.py (get_runtime) · proxy /health /health/keys
                     │
              EXISTING ATLAS SERVICES (proxy :8788, CLI, keypool)
```

### Shared management layer (no duplicated logic)

- `services/config_service.py` — resolves config from `proxy.config` (import, not copy), reads/writes `runtime_provider.json` exactly like `atlas restart`.
- `services/key_service.py` — reads the live key file (same path `proxy.config.KEY_FILE`), appends dedupes exactly like `atlas import-key`. Never writes secrets to DB; only masked fingerprints + metadata.
- `services/runtime_service.py` — wraps `atlas/bin/runtime.py get_runtime()` for stop/start/status/logs. Allow-listed actions only.
- `services/proxy_client.py` — talks to the proxy's `/health`, `/health/keys`, `/stats` for live state.
- `services/audit_service.py` — writes security-relevant events to SQLite.

### Persistence (SQLite, `data/dashboard/atlas.db`)

Dashboard-owned only: `admin`, `sessions`, `audit_log`, `setup_token`, `mfa`, `metric_samples`.
Proxy/config/keys stay in their existing files — **single source of truth preserved**.

### Independent lifecycle

Dashboard runs as its own process (`dashboard.api.main`). Proxy crash ≠ dashboard down and vice-versa. Dashboard reads proxy state over HTTP and shells to the runtime layer for control.

### TLS on 8080

`0.0.0.0:8080` terminates TLS (reverse proxy or built-in) → `127.0.0.1:8090` app. Cert/domain is a deployment input; see SECURITY.md for the exact blocker if absent.

## Frontend structure

- `src/design/tokens.ts` — the single colour/typography/spacing system (CSS vars in `styles/tokens.css`).
- `src/components/` — primitives (Surface, StatCard, Sidebar, TopBar, Chart wrappers, Table, Badge, Toast, Modal).
- `src/pages/` — Overview (this checkpoint) + placeholders for the rest.
- `src/lib/api.ts` — typed API client, credentials: include.
- No global state library; React Query-lite via hooks + AbortController.
