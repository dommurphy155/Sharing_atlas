# Risks and Security

## Update Safety (Critical)

An update must never:

- Delete API keys (`data/openrouter_data/openroute_keys.txt`).
- Replace `.env` (ports, dashboard secrets).
- Reset default model / provider (`data/proxy_data/runtime_provider.json`).
- Remove dashboard DB (`data/dashboard/atlas.db`).
- Start a second proxy process.
- Replace a working `.venv` in place.

### Mitigations

1. Install each app version to `~/.atlas/install/<version>/` (immutable).
2. Canonical data in `~/.atlas/data/` (symlinked into version dir).
3. Update = download new version + new venv + flip pointer. Old version kept.
4. Health-check new version before flipping pointer.
5. Rollback = flip pointer back.

## Security Risks

| Risk                                  | Mitigation                                                           |
| ------------------------------------- | -------------------------------------------------------------------- |
| Supply chain (npm package compromise) | Pin dependencies; publish from CI with provenance; sign releases     |
| Malicious tarball from GitHub         | Verify SHA-256 against checksum stored in npm package; use HTTPS     |
| `postinstall` abuse                   | Keep postinstall to a print-only hint; no network, no exec           |
| PATH hijack                           | Node shim uses absolute paths to venv python                         |
| API key exposure                      | Existing 0600 perms preserved; npm layer never reads keys            |
| Running as root                       | npm global install as root is user's choice; app itself is sudo-free |
| Dependency confusion                  | Scoped package `@atlas-ai/cli`; no generic names                     |

## Reliability Risks

| Risk                         | Mitigation                                                                |
| ---------------------------- | ------------------------------------------------------------------------- |
| Python missing               | Reuse `install.ps1`/`install.sh` detection; clear error with install link |
| Network failure mid-download | Atomic temp file + rename; resume not required for v1                     |
| venv creation fails          | Fall back to system python with `--user` pip (warn)                       |
| Port already in use          | Existing wizard/health-check handles                                      |
| Stale runtime.json           | Existing `runtime.py` re-detects if persisted mode unusable               |

## Rollback

- `atlas update` keeps previous version dir.
- If new version fails health check, pointer is not flipped.
- Manual rollback: edit `~/.atlas/runtime.json` to previous version, `atlas restart`.
