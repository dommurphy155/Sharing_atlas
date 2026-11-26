# CLAUDE.md — Atlas

Atlas = a translation proxy (FastAPI) that fronts OpenRouter / HuggingFace / NVIDIA,
plus now a **premium admin dashboard** (React/Vite + FastAPI) that is a second
interface onto the _same_ installation.

## Repo layout

- `proxy/` — the existing AI-request proxy. **Do not rewrite or replace.**
- `atlas/bin/` — the `atlas` CLI (argparse) + runtime strategy (`runtime.py`). **Do not break.**
- `setup/`, `install.sh`, `install.ps1` — installers. Leave alone unless asked.
- `dashboard/api/` — dashboard FastAPI app (shared management layer, auth, audit).
- `dashboard/web/` — React + TS + Vite frontend.
- `docs/dashboard/` — architecture, security, threat model, test plan, progress.

## Commands

- Proxy: `atlas start|stop|restart|status|logs` (runtime resolved via `data/runtime.json`).
- Dashboard API (prod build): serve `dashboard/web/dist` as static + API on `0.0.0.0:8080`, TLS via `DASHBOARD_PUBLIC_TLS`.
- Dashboard API (dev): `.venv/bin/python -m dashboard.api.main` (binds `0.0.0.0:8080`).
- Frontend dev: `cd dashboard/web && npm run dev`
- Frontend build: `cd dashboard/web && npm run build`
- Tests: `.venv/bin/python -m pytest tests/ -q` (existing) — dashboard tests under `dashboard/api/tests/`.
- Visual QA: `dashboard/web/scripts/shoot.py` drives Chrome via CDP `http://localhost:9222`.

## Hard rules

1. Preserve proxy + CLI behaviour. Smallest sensible change only.
2. One keypool, one config state, one runtime — dashboard and CLI share them.
3. Never expose raw API keys / secrets to the browser. Mask always.
4. All admin endpoints auth+authz server-side. Frontend is never a security control.
5. Controlled runtime restarts need the existing runtime layer; no arbitrary shell.
6. Dashboard process is independent of the proxy process.
7. Do not fabricate metrics — show real data or a proper empty state.

## Progress

See `docs/dashboard/IMPLEMENTATION_PLAN.md` for the live phase checklist.
