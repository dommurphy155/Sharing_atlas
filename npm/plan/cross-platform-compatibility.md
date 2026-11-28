# Cross-Platform Compatibility

## Shared Requirements

- Node.js 18+ (for the npm shim).
- Python 3.10+ (for Atlas itself).
- Network access on first install / update.
- ~500 MB disk (venv + deps).

## Windows

### Verified existing behaviour

- `install.ps1` discovers Python via `python`, `python3`, `py`, then `%LOCALAPPDATA%\Programs\Python\Python3xx`, then WinGet.
- `atlas/bin/runtime.py` uses `scheduled-task` (per-user logon task) — no admin required.
- `setup/_supervisor.py::make_windows_shim` writes `%USERPROFILE%\.local\bin\atlas.cmd` + POSIX `atlas` wrapper and registers PATH via `winreg`.
- Venv python: `.venv\Scripts\python.exe`.

### npm-specific notes

- `bin/atlas.js` must spawn via `cmd.exe /c` or use `child_process.spawn` with `shell: true` on Windows.
- Path with spaces: wrap in quotes; existing shims already handle this.
- PATH registration: npm global bin is already on PATH; the node shim just needs to call the venv python.
- No Git Bash assumption: the node shim works from PowerShell, cmd, and Git Bash.
- Windows service: not required initially; scheduled-task + detached process is sufficient.

### Risks

- Multiple Python installs (Store alias vs real). Mitigation: reuse `install.ps1` discovery logic or call it.
- ExecutionPolicy: node shim bypasses PowerShell policy entirely.

## Linux

### Verified existing behaviour

- `install.sh` checks `python3` ≥ 3.10, creates `.venv`, pip installs, runs wizard.
- Runtime priority: `systemd-user` → `tmux` → `nohup` → `manual`.
- Systemd user unit written to `~/.config/systemd/user/atlas-proxy.service`.
- CLI shim at `~/.local/bin/atlas` (POSIX sh wrapper).
- Root and non-root both supported; sudo never required.

### npm-specific notes

- `bin/atlas.js` spawns venv python directly.
- Install dir `~/.atlas/` is user-writable; no root.
- If user has existing systemd-user unit from prior install, npm install should detect and reuse, not duplicate.

### Risks

- Distros without `python3-venv` (Debian minimal). Mitigation: clear error message with apt/dnf hint.
- `~/.local/bin` not on PATH. Mitigation: existing `_persist_posix_path` appends to rc files; npm postinstall can print a hint.

## macOS

### Verified existing behaviour

- `install.sh` treats darwin as `macos`.
- Runtime priority: `launchd` (LaunchAgent in `~/Library/LaunchAgents/com.atlas.proxy.plist`) → `tmux` → `nohup`.
- Venv python: `.venv/bin/python`.
- Both Intel and Apple Silicon supported (pure Python + uvloop wheel).

### npm-specific notes

- Same node shim as Linux.
- `launchctl bootstrap gui/<uid>` used; no sudo.
- Homebrew Python and system Python both work; `python3` on PATH is sufficient.

### Risks

- macOS 12+ requires notarization only for signed apps; a node shim invoking python is unaffected.
- Apple Silicon: ensure `uvloop` wheel available (it is for 3.10+).

## Summary Table

| Concern          | Windows                    | Linux                   | macOS                |
| ---------------- | -------------------------- | ----------------------- | -------------------- |
| Python discovery | `install.ps1` logic        | `python3` on PATH       | `python3` on PATH    |
| Venv path        | `.venv\Scripts\python.exe` | `.venv/bin/python`      | `.venv/bin/python`   |
| Supervisor       | scheduled-task             | systemd-user/tmux/nohup | launchd/tmux/nohup   |
| CLI shim         | `atlas.cmd` + sh           | `~/.local/bin/atlas`    | `~/.local/bin/atlas` |
| Root needed      | No                         | No                      | No                   |
| npm shim spawn   | `shell:true` / cmd         | direct                  | direct               |
