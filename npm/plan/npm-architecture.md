# Proposed npm Architecture

## Principle

**npm = distribution + CLI management layer. Python = the application.** No rewrite.

```
┌─────────────────────────────────────────────────────────────┐
│  User shell                                                  │
│    npm install -g @atlas-ai/cli                              │
│    atlas setup | start | stop | status | update | uninstall  │
└──────────────────────────┬──────────────────────────────────┘
                           │  atlas (node shim on PATH)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  npm package: @atlas-ai/cli                                  │
│  npm/ (new dir in repo)                                      │
│    bin/atlas.js          # node entry, forwards argv         │
│    lib/install.js        # fetch source, create venv, deps   │
│    lib/paths.js          # resolve install dir, venv python  │
│    lib/update.js         # version check, safe replace       │
│    package.json          # bin: {"atlas": "bin/atlas.js"}    │
└──────────────────────────┬──────────────────────────────────┘
                           │  child_process.spawn
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Existing Python CLI (unchanged)                             │
│  <install>/atlas/bin/atlas  <cmd> ...                        │
│    ├─ setup      → python -m setup.wizard                    │
│    ├─ start      → runtime.start()                           │
│    ├─ stop       → runtime.stop()                            │
│    ├─ restart    → runtime.stop()+start()                    │
│    ├─ status     → runtime.status() + /health                │
│    ├─ update     → npm-level update (node) or git pull       │
│    └─ uninstall  → runtime.cleanup() + remove install dir    │
└─────────────────────────────────────────────────────────────┘
```

## Source Acquisition Options

| Option                                             | Maintenance             | Reliability                    | Security                          | Update                           | Verdict           |
| -------------------------------------------------- | ----------------------- | ------------------------------ | --------------------------------- | -------------------------------- | ----------------- |
| A. Bundle Python source in npm tarball             | High (duplicate source) | High                           | Medium (stale if not republished) | Requires npm publish per release | Reject            |
| B. Download versioned tarball from GitHub releases | Low                     | High (if releases exist)       | High (checksum + TLS)             | `npm update` + re-download       | **Recommended**   |
| C. `git clone` at install                          | Low                     | Medium (requires git, network) | Medium                            | `git pull`                       | Fallback          |
| D. Rewrite in Node                                 | N/A                     | N/A                            | N/A                               | N/A                              | Reject (explicit) |

**Recommendation: B.** Publish `package.json` with no bundled source. `bin/atlas.js` on first run downloads `https://github.com/<owner>/<repo>/archive/refs/tags/v<ver>.tar.gz`, verifies SHA-256 against a value stored in the npm package, extracts to `~/.atlas/install/<ver>/`, creates `.venv`, installs `requirements.txt`, then execs the existing CLI.

## Install Directory

```
~/.atlas/
  install/
    1.2.3/              # versioned source tree (immutable)
      .venv/            # per-version venv
      data/             # symlink or copy-on-write from ~/.atlas/data
  data/                 # canonical user data (keys, db, logs, .env)
  runtime.json          # active version pointer
```

- User data lives in `~/.atlas/data`, never inside a version dir.
- `atlas update` downloads new version, creates new venv, migrates/symlinks data, flips pointer, keeps old version for rollback.
- Uninstall removes `~/.atlas` entirely (with confirmation).

## Update Flow

1. `atlas update` (node) reads installed version from `~/.atlas/runtime.json`.
2. Queries npm registry for `@atlas-ai/cli` latest, or GitHub releases for app version.
3. Downloads new tarball, verifies checksum.
4. Extracts to `~/.atlas/install/<new>/`.
5. Creates venv, `pip install -r requirements.txt`.
6. Runs `<new>/.venv/bin/python -m setup.wizard --check` (non-interactive).
7. Atomically updates `~/.atlas/runtime.json` pointer.
8. Prints "Run `atlas restart` to activate." Does NOT auto-restart.
9. Old version retained until next successful update or explicit cleanup.

**Never touched:** `~/.atlas/data/`, `.env`, keys, DB, ports, model.

## Why This Is Small

- Zero changes to `proxy/`, `dashboard/`, `setup/`, `atlas/bin/atlas`, `install.sh`, `install.ps1`.
- All new code lives in `npm/`.
- The existing CLI already has every lifecycle command needed.
