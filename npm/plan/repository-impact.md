# Repository Impact Assessment

## Strict Requirement

Only create files under `npm/`. Do not modify existing application files.

## Proposed New Files (all under `npm/`)

| Path                   | Purpose                                                                             |
| ---------------------- | ----------------------------------------------------------------------------------- |
| `npm/package.json`     | npm manifest, `bin: {"atlas": "bin/atlas.js"}`, `postinstall` hint                  |
| `npm/bin/atlas.js`     | Node entry; first-run check; forwards argv to venv python running `atlas/bin/atlas` |
| `npm/lib/paths.js`     | Resolve `~/.atlas`, install dir, venv python, data dir                              |
| `npm/lib/install.js`   | Download tarball, verify checksum, extract, create venv, pip install                |
| `npm/lib/update.js`    | Version compare, download new, migrate pointer, rollback info                       |
| `npm/lib/uninstall.js` | Stop services via existing CLI, remove `~/.atlas` after confirm                     |
| `npm/README.md`        | npm package readme                                                                  |

## Existing Files: No Change Required

| File                         | Reason                                                                          |
| ---------------------------- | ------------------------------------------------------------------------------- |
| `atlas/bin/atlas`            | Already forwards to runtime; npm shim calls it                                  |
| `atlas/bin/runtime.py`       | Supervisor abstraction reused as-is                                             |
| `atlas/bin/dashboard_cli.py` | Dashboard lifecycle reused                                                      |
| `setup/core.py`              | Bootstrap reused                                                                |
| `setup/wizard.py`            | First-run UX reused                                                             |
| `setup/_supervisor.py`       | Shim generation reused (npm uses its own node shim instead)                     |
| `install.sh` / `install.ps1` | Can be called by npm install if source is cloned, or replaced by node installer |
| `proxy/**`                   | Application untouched                                                           |
| `dashboard/**`               | Application untouched                                                           |
| `requirements.txt`           | Pinned by npm installer                                                         |

## Existing Files: Potential Small Modification (Optional, Avoid If Possible)

| File              | Change                                           | Risk | Alternative                                     |
| ----------------- | ------------------------------------------------ | ---- | ----------------------------------------------- |
| `atlas/bin/atlas` | Add `update` subcommand that shells out to `npm` | Low  | Node shim intercepts `update` before forwarding |
| `setup/wizard.py` | Add `--non-interactive` flag for CI              | Low  | Env vars + piped stdin already work             |

**Recommendation:** Avoid both. Handle `update` in the node layer; use piped stdin for non-interactive.

## Obsolete Files

None.

## Summary

- **New code:** 7 files in `npm/`.
- **Modified existing files:** 0 (goal).
- **Application behaviour change:** 0.
