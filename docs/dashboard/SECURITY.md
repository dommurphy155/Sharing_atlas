# Security — Atlas Dashboard

Public administrative interface controlling infra + API credentials.
Reference: OWASP ASVS 5.0.0, OWASP Top 10:2025. Treat as internet-facing.

## Authentication

- Argon2id password hashing (`argon2-cffi`), parameters OWASP-recommended (m=64MiB, t=3, p=4 baseline).
- No default password. First admin created only via **one-time setup token** minted by CLI (`atlas dashboard setup-token`), printed to a local terminal, stored as a hash, single-use, 15-min expiry, atomic claim (row lock / conditional update), disabled permanently after admin exists.
- Server-side sessions in SQLite; opaque session id in `HttpOnly; Secure; SameSite=Strict` cookie. Session rotates on login (fixation defence). Idle + absolute expiry. Revocation list.
- TOTP MFA (RFC 6238) required at setup; recovery codes hashed.
- Rate limiting + lockout on login (per-IP + per-account), constant-time compare, generic errors (no user enumeration).
- Reauthentication (fresh password) required for sensitive ops: password change, MFA reset, key import/remove, config write, restart, update.

## Authorisation

- All `/api/*` except `/api/auth/status`, `/api/setup/*` require a valid session; every mutating route re-checks role=admin server-side. Frontend guards are cosmetic only.

## Transport / network

- External `0.0.0.0:8080` HTTPS only. App binds `127.0.0.1:8090`. No plaintext password over HTTP — insecure public access stays OFF if no cert.
- Security headers: CSP (self + inline nonce), HSTS (when HTTPS domain), X-Content-Type-Options, X-Frame-Options DENY, Referrer-Policy, Permissions-Policy.
- Strict Host allowlist, restrictive CORS (same-origin only), CSRF double-submit token on mutations.

## Secrets

- Raw keys never leave the server. API returns masked (`sk-…abcd`) + fingerprint (sha256 prefix) only.
- Key file perms checked/kept 0600. Never log keys/tokens/passwords (prettylog already redacts `_RE_KEY`).
- No secrets in error strings or audit rows.

## Injection / SSRF / traversal

- SQLite via parameterised queries only. Pydantic validation at every boundary.
- No user-controlled URLs fetched server-side (no SSRF surface). No user paths → traversal.
- No arbitrary shell: runtime actions are a fixed allow-list mapped to `runtime.py` methods.

## Audit

Log: login success/fail, password/MFA change, session revoke, key add/remove/enable/disable, config change, restart, update, backup/restore — without secrets.

## Verified vs residual

See TEST_PLAN.md → Security tests. Residual risks documented at end of build (e.g. cert provisioning, multi-admin nuance).

## Known deployment blocker (must document honestly)

If no trusted TLS cert + domain is present on the host, public HTTPS on 8080 cannot be safely enabled. In that case: build + internal listener complete, external public access kept disabled, and the exact requirement stated rather than exposing passwords over plaintext.
