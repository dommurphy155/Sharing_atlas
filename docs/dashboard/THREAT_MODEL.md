# Threat Model — Atlas Dashboard

Assets: OpenRouter/HF/NVIDIA API keys · proxy availability · admin credentials · audit integrity · config/keys.

Trust boundary: internet → 8080 TLS → app 127.0.0.1:8090. Admin is sole privileged actor.

| #   | Threat                            | Vector                            | Mitigation                                                                       | Residual                             |
| --- | --------------------------------- | --------------------------------- | -------------------------------------------------------------------------------- | ------------------------------------ |
| T1  | Unauthorised first-admin claim    | Attacker reaches setup page first | One-time CLI-minted hashed token, single-use, expiry, setup disabled after admin | token left on screen                 |
| T2  | Credential stuffing / brute force | login endpoint                    | rate limit + lockout + constant-time + generic errors                            | distributed slow attack              |
| T3  | Session hijack / fixation         | cookie theft, pre-auth fixation   | HttpOnly+Secure+SameSite=Strict, rotate on login, expiry, revoke                 | XSS stolen cookie (mitigated by CSP) |
| T4  | CSRF on state change              | cross-site form                   | double-submit CSRF token + SameSite                                              | —                                    |
| T5  | Key exfiltration via API/UI       | read endpoint                     | masked only, fingerprint, never raw in responses/logs                            | server-side compromise               |
| T6  | Arbitrary command execution       | command centre                    | fixed allow-list → runtime.py methods; no shell passthrough                      | —                                    |
| T7  | Privilege escalation              | role checks client-only           | server-side authz on every mutating route                                        | —                                    |
| T8  | Injection                         | SQL/Pydantic boundaries           | parameterised queries, schema validation                                         | —                                    |
| T9  | Info disclosure in errors         | stack traces                      | generic client errors, detailed server logs                                      | —                                    |
| T10 | Dashboard outage blocks recovery  | coupling                          | independent process; reads proxy over HTTP                                       | —                                    |
| T11 | Plaintext password exposure       | HTTP listener                     | HTTPS only; disable public if no cert                                            | cert dependency                      |
| T12 | Log secret leakage                | app logs                          | redaction, audit excludes secrets                                                | —                                    |
