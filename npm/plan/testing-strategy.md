# Testing Strategy

## Matrix

| Platform                  | Clean install | Upgrade | Existing install | Failed install | Uninstall |
| ------------------------- | ------------- | ------- | ---------------- | -------------- | --------- |
| Windows 10                | ✓             | ✓       | ✓                | ✓              | ✓         |
| Windows 11                | ✓             | ✓       | ✓                | ✓              | ✓         |
| Ubuntu 22.04/24.04        | ✓             | ✓       | ✓                | ✓              | ✓         |
| Debian 12                 | ✓             | ✓       | ✓                | ✓              | ✓         |
| Fedora 39+                | ✓             | ✓       | ✓                | ✓              | ✓         |
| macOS Intel (12+)         | ✓             | ✓       | ✓                | ✓              | ✓         |
| macOS Apple Silicon (12+) | ✓             | ✓       | ✓                | ✓              | ✓         |

## Per-Phase Tests

### Phase 1: npm shim

- `npm install -g .` from `npm/` dir.
- `atlas --version` forwards correctly.
- `atlas --help` shows existing help.
- Unknown command fuzzy-matches.

### Phase 2: Installer

- Fresh machine: `atlas setup` creates `~/.atlas/install/<v>/`, venv, deps.
- Existing Atlas repo on disk: npm install does not touch it.
- No Python: clear error, non-zero exit.
- With `--ignore-scripts`: `atlas` first run still works.

### Phase 3: Lifecycle

- `atlas start` → `/health` 200.
- `atlas stop` → process gone, pidfile removed.
- `atlas restart` → new pid, healthy.
- `atlas status` → correct table.
- `atlas dashboard start/status/stop`.

### Phase 4: Update

- Install v1, add keys, start, `atlas update` to v2.
- Keys preserved, `.env` preserved, DB preserved, provider/model preserved.
- Failed update (bad checksum): old version still active.
- Rollback: pointer flip restores v1.

### Phase 5: Uninstall

- `atlas uninstall` stops services, removes `~/.atlas` after confirm.
- `npm uninstall -g @atlas-ai/cli` leaves data intact.

## CI Hooks

- GitHub Actions matrix: `windows-latest`, `ubuntu-latest`, `macos-latest`, `macos-13` (Intel).
- Each job: `npm ci`, `npm test`, `node bin/atlas.js --version`.
- Integration job: install to temp `HOME`, run `setup` with piped keys, curl `/health`.
