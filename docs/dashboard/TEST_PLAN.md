# Test Plan — Atlas Dashboard

## Unit / API

- setup token: mint, single-use, expiry, atomic claim, disabled-after-admin
- password: argon2 verify, strength rejection, no plaintext stored
- session: create, rotate-on-login, expiry, revoke, cookie flags
- csrf: mutation without token → 403; with token → ok
- authz: unauthenticated → 401 on all protected routes
- key masking: responses contain masked only, never raw
- rate limit: N failed logins → 429/lock
- config service: write-reflects-CLI-state; key append dedupes like CLI

## Security

- headers present (CSP, HSTS, XFO, XCTO)
- no secrets in logs/audit/errors
- SSRF/injection: schema rejection of malformed bodies
- key file perms

## Functional (browser, CDP)

- setup → login → overview happy path
- navigation, active states, responsive drawer
- loading / empty / error / populated states
- console errors = 0, failed requests = 0

## Regression (must pass, unchanged)

- `.venv/bin/python -m pytest tests/` (existing 3 files)
- `atlas status` works; proxy still serves on :8788

## Visual QA (mandatory loop)

- Screenshot every page desktop 1440×900, tablet 1024, mobile 390×844 → `artifacts/dashboard-review/<page>/<iter>/`
- Inspect each, fix, rescreenshot until polished.

## Acceptance

Machine-checkable: pytest green, existing tests green, HTTP codes as specified, headers present, screenshots exist and inspected.
