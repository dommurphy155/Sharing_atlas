# Installation Workflow

## User Journey

```
$ npm install -g @atlas-ai/cli
added 1 package in 3s

$ atlas --version
atlas 1.2.3 (app 1.2.3)

# First run — no ~/.atlas yet
$ atlas setup
Welcome to Atlas!
Checking your system...
[OK] Operating system detected: linux
[OK] Python detected: 3.12.1
[OK] Installation directory: ~/.atlas/install/1.2.3
[OK] Dependencies installed
Starting Atlas setup...
  ... existing rich wizard (keys, proxy start, optional dashboard) ...
Done. Proxy running on http://127.0.0.1:8788

$ atlas status
... existing status table ...

$ atlas start
$ atlas stop
$ atlas restart
$ atlas update
$ atlas uninstall
```

## First-Run Detection

`bin/atlas.js` checks for `~/.atlas/runtime.json`. If missing → print banner and exec `atlas setup` (which runs the existing Python wizard). If present → forward argv to existing CLI.

## Should `postinstall` Auto-Run Setup?

| Approach                                         | Pros              | Cons                                                                                             | Verdict                |
| ------------------------------------------------ | ----------------- | ------------------------------------------------------------------------------------------------ | ---------------------- |
| `postinstall` runs wizard                        | Zero-friction     | Breaks with `--ignore-scripts`; hangs in CI; bad for global root installs; runs on every upgrade | Reject                 |
| `postinstall` prints "Run `atlas setup`"         | Safe, predictable | One extra command                                                                                | **Recommended**        |
| No postinstall, first `atlas` run triggers setup | Lazy, clean       | Slightly surprising                                                                              | Acceptable alternative |

**Recommendation:** `postinstall` prints a one-line hint only. Setup runs on first `atlas setup` or first bare `atlas` invocation when no install exists.

## Upgrade Path

- `npm update -g @atlas-ai/cli` updates the node shim.
- `atlas update` updates the Python app (downloads new version, new venv, flips pointer).
- Existing `data/`, `.env`, keys, DB are preserved.
- If `atlas update` fails mid-way, old version remains active.

## Uninstall

- `atlas uninstall` stops proxy/dashboard, removes runtime config (existing `runtime.cleanup()`), then prompts before deleting `~/.atlas`.
- `npm uninstall -g @atlas-ai/cli` removes the node shim; leaves `~/.atlas` intact (data preserved) unless user ran `atlas uninstall`.
