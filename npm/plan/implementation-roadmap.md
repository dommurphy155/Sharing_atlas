# Implementation Roadmap

## Phase 0 — Prep (no code)

- [ ] Create GitHub releases with versioned tarballs + SHA-256.
- [ ] Decide npm scope (`@atlas-ai/cli`) and register.
- [ ] Add `npm/` to `.gitignore` exceptions if needed (it must be committed).

## Phase 1 — Minimal npm shim (1–2 days)

- [ ] `npm/package.json` with `bin`, `engines`, `postinstall` hint.
- [ ] `npm/bin/atlas.js`: resolve `~/.atlas`, detect first run, forward argv.
- [ ] `npm/lib/paths.js`: cross-platform path resolution.
- [ ] Test: `npm install -g .`, `atlas --version`, `atlas --help`.

## Phase 2 — Source installer (2–3 days)

- [ ] `npm/lib/install.js`: download tarball, verify SHA-256, extract to `~/.atlas/install/<v>/`.
- [ ] Create venv, `pip install -r requirements.txt`.
- [ ] Symlink/copy `~/.atlas/data` into version dir.
- [ ] Write `~/.atlas/runtime.json` pointer.
- [ ] Test on clean Windows/Linux/macOS.

## Phase 3 — Lifecycle forwarding (1 day)

- [ ] Forward `setup/start/stop/restart/status/logs/config/doctor/import-key/dashboard` to existing CLI.
- [ ] Verify `atlas setup` runs existing wizard unchanged.
- [ ] Test start/stop/restart/status on all platforms.

## Phase 4 — Update + rollback (2 days)

- [ ] `npm/lib/update.js`: version compare, download, health-check, pointer flip.
- [ ] Keep previous version dir.
- [ ] Test upgrade with preserved keys/.env/DB.
- [ ] Test failed update + rollback.

## Phase 5 — Uninstall (0.5 day)

- [ ] `npm/lib/uninstall.js`: call existing `runtime.cleanup()`, confirm, remove `~/.atlas`.
- [ ] `npm uninstall` leaves data unless `atlas uninstall` was run.

## Phase 6 — Publish + CI (1 day)

- [ ] GitHub Actions matrix build/test.
- [ ] `npm publish` from CI with provenance.
- [ ] Install-from-registry smoke test.

## Total: ~8–10 working days for one engineer.

## What to Implement First

1. Phase 1 shim + Phase 2 installer for **Linux/macOS only**.
2. Validate that `atlas setup` → existing wizard → proxy healthy.
3. Add Windows.
4. Add update/rollback.
5. Publish.

## Definition of Done

- `npm install -g @atlas-ai/cli && atlas setup` works on Windows 11, Ubuntu 24.04, macOS Sonoma (Intel + ARM).
- `atlas start/stop/restart/status/update/uninstall` all work.
- Existing `data/`, `.env`, keys, DB survive updates.
- Zero changes to `proxy/`, `dashboard/`, `setup/`, `atlas/bin/atlas`.
