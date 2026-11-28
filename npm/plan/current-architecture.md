# Current Architecture (Verified)

All facts below were read directly from the repository at `/root/atlas`. Paths are relative to repo root.

## 1. Repository Layout

```
atlas/
  bin/
    atlas              # Python CLI entry point (executable, 32KB)
    dashboard_cli.py   # Dashboard subcommands
    runtime.py         # Process supervisor abstraction (systemd/launchd/tmux/nohup/scheduled-task)
    models.py          # Model picker
    switch.py          # Interactive provider/model switcher
dashboard/
  api/                 # FastAPI admin API (main.py, config.py, routers/, services/)
  deploy/
    atlas-dashboard.service   # systemd unit for dashboard
  web/                 # React frontend (dist/ served by API)
data/                  # Runtime state (gitignored)
  openrouter_data/openroute_keys.txt   # API keys (0600)
  proxy_data/runtime_provider.json     # Active provider/model
  dashboard/atlas.db                   # Admin, sessions, audit log
  run.log / run.pid / dashboard.log / dashboard.pid
proxy/
  main.py              # FastAPI app + uvicorn entry (`python -m proxy.main`)
  config.py            # Env-overridable config (LISTEN_HOST/PORT, PROVIDER, KEY_FILE...)
  routes.py            # /v1/chat/completions, /v1/messages, /v1/responses, /health
  proxy.py             # HTTP forwarding, SSE streaming, retry
  keypool.py           # Atomic round-robin key selector with health states
  translation.py       # Anthropic <-> OpenAI protocol translation
  system_prompt.py     # Structural harness filtering + override injection
setup/
  core.py              # Stdlib-only bootstrap (venv, deps, keys, health)
  wizard.py            # Interactive installer (rich)
  dashboard_setup.py   # Admin creation + detached dashboard start
  _supervisor.py       # POSIX/Windows CLI shim generation
  linux_setup.py / mac_setup.py / windows_setup.py
install.sh             # Linux/macOS bootstrap -> wizard
install.ps1            # Windows bootstrap -> wizard
requirements.txt       # fastapi, uvicorn, httpx, h2, orjson, rich, argon2-cffi, pyotp, bcrypt, cryptography, uvloop
```

## 2. Entry Points

| Context             | Command                                  |
| ------------------- | ---------------------------------------- |
| Proxy               | `.venv/bin/python -m proxy.main`         |
| CLI                 | `.venv/bin/python atlas/bin/atlas <cmd>` |
| Wizard              | `.venv/bin/python -m setup.wizard`       |
| Dashboard           | `.venv/bin/python -m dashboard.api.main` |
| Linux/macOS install | `bash install.sh`                        |
| Windows install     | `powershell -File install.ps1`           |

## 3. CLI Command Surface (`atlas/bin/atlas`)

Verified subcommands: `install`, `uninstall`, `start`, `stop`, `restart`, `switch`, `status`/`st`, `logs`/`log`, `doctor`, `check`, `config`, `version`/`--version`/`-v`, `import-key`, `import-key-file`, `dashboard <start|stop|restart|status|logs|setup-token|change-password|change-port>`.

Fuzzy matching is built in (`difflib.get_close_matches`, cutoff 0.5).

Repo discovery: walks up from `__file__` looking for `proxy/main.py`; falls back to CWD.

## 4. Bootstrap Chain

`atlas/bin/atlas` `_bootstrap()`:

1. Try `import rich`. If missing:
2. Create `.venv` if absent (`python -m venv`).
3. `pip install -r requirements.txt` (or just `rich`).
4. `os.execv` re-exec into `.venv/bin/python`.

`install.sh` / `install.ps1` do the same venv+pip work, then `exec python -m setup.wizard`.

## 5. Configuration Storage

| Concern                | Location                                                        | Notes                                                                |
| ---------------------- | --------------------------------------------------------------- | -------------------------------------------------------------------- |
| Proxy host/port        | `proxy/config.py` via `LISTEN_HOST`/`LISTEN_PORT` env           | Defaults `0.0.0.0:8788`                                              |
| Dashboard host/port    | `dashboard/api/config.py` via `DASHBOARD_HOST`/`DASHBOARD_PORT` | Defaults `127.0.0.1:8080`                                            |
| Runtime provider/model | `data/proxy_data/runtime_provider.json`                         | Written by CLI, read at import                                       |
| Keys                   | `data/openrouter_data/openroute_keys.txt` (0600)                | Hot-reloaded every 5s                                                |
| `.env`                 | Repo root                                                       | Loaded by systemd `EnvironmentFile`, read by `dashboard change-port` |
| Runtime choice         | `data/runtime.json`                                             | Persisted by `runtime.py`                                            |
| Dashboard DB           | `data/dashboard/atlas.db`                                       | Admin, sessions, audit                                               |

## 6. Process Supervision (`atlas/bin/runtime.py`)

Selection priority: `systemd-user` → `launchd` (macOS) → `tmux` → `nohup` → `scheduled-task` (Windows) → `manual`.

Explicitly **sudo-free**: system-level systemd is never auto-selected. Root/non-root both supported.

Each runtime implements: `start`, `stop`, `status`, `logs`, `cleanup`, `env_summary`.

## 7. OS Detection

- `setup/core.py::detect_os()` → `linux` / `macos` / `windows` / raw
- `atlas/bin/runtime.py::detect_os()` → same, plus `other`
- Windows Python discovery (`install.ps1`): `python`, `python3`, `py`, then `%LOCALAPPDATA%\Programs\Python\Python3xx`, then WinGet install of Python 3.12.

## 8. Existing Installations

- venv presence: `.venv/bin/python` (POSIX) or `.venv/Scripts/python.exe` (Windows)
- deps importable: `python -c "import fastapi, uvicorn, httpx, orjson"`
- keys: `data/openrouter_data/openroute_keys.txt` non-empty
- proxy running: runtime `status()` + `/health`
- CLI on PATH: `shutil.which("atlas")`
- dashboard admin: `dashboard.api.security.admin_exists()`

## 9. Reusable Without Modification

- `proxy/` — entire application
- `dashboard/` — entire application
- `atlas/bin/atlas` — CLI (npm wrapper just forwards argv)
- `atlas/bin/runtime.py` — supervisor abstraction
- `setup/` — wizard and bootstrap
- `install.sh` / `install.ps1` — OS bootstraps
- `requirements.txt`

The npm layer only needs to: locate/prepare Python, fetch source, create venv, install deps, and exec the existing CLI.
