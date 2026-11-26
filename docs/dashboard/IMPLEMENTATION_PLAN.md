# Implementation Plan — Atlas Dashboard

Live checklist. Update as work advances so context can be resumed safely.

## Phase 1 — Audit & design ✅

- [x] Audit proxy, keypool, routes, translation, config, CLI, runtime, logging, tests, storage
- [x] Record deployment (tmux runtime, proxy :8788, 1170 keys)
- [x] Architecture doc, security doc, threat model, CLAUDE.md
- [x] Design system tokens defined (tokens.ts / tokens.css)

## Phase 2 — Foundation (this checkpoint)

- [x] Dashboard API skeleton (FastAPI, SQLite, sessions, audit)
- [x] Shared management layer services (config/key/runtime/proxy/audit)
- [x] Argon2id auth, one-time setup token, TOTP MFA, sessions
- [x] First-time setup flow + login
- [x] Independent lifecycle entrypoint
- [x] React/Vite/TS app, design system, sidebar + topbar
- [x] Overview page — real data from /health, /health/keys, /stats
- [x] Visual QA loop (CDP) — desktop 1440×900 + mobile 390×844, captured & inspected, zero console errors
- [x] Security tests (auth bypass 401, CSRF rejection, rate limit/lockout proven, key masking, security headers)

## Phase 3 — Admin (next)

- [ ] Keypool management · Models & Routing · Configuration · Proxy/System controls

## Phase 4 — Monitoring

- [ ] Live Requests (SSE) · Logs · Analytics

## Phase 5 — Visual refinement across all pages

## Phase 6 — Security & reliability hardening

## Phase 7 — Deployment (TLS 8080, firewall, recovery)

## Decisions

- SQLite at `data/dashboard/atlas.db` (auth/audit/metrics only; keys/config stay in existing files).
- App listener `127.0.0.1:8090`; public TLS `0.0.0.0:8080`.
- Design tokens in one place; components reference tokens only.
- Charts: Recharts. Icons: lucide-react. Component primitives hand-built (no heavy UI kit) for a distinctive look.
