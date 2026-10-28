#!/usr/bin/env bash
# Atlas proxy installer — Linux & macOS first-run bootstrap.
#
# Bootstraps a self-contained install at --prefix (default ~/atlas):
#   system python3 -> venv -> deps -> interactive wizard (setup/wizard.py).
# The wizard then handles harness pick, key import, auto-start, verify, launch.
#
# Usage:
#   install.sh                         Interactive install to ~/atlas
#   install.sh --prefix /path          Install to a chosen dir
#   install.sh --dry-run               Preview without changes
#   install.sh --check                 Diagnostics on an existing install
#   install.sh --uninstall           Stop + remove the install
#   install.sh --no-harness --provider openrouter --skip-keys   (headless flags)
#
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1   # never emit .pyc (user preference)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$OS" in
  linux*) OS=linux;; darwin*) OS=macos;; *) echo "[!] unsupported os: $OS" >&2; exit 1;;
esac

DRY_RUN=0; CHECK=0; UNINSTALL=0; PREFIX=""; NO_HARNESS=0; PROVIDER=""; SKIP_KEYS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift;;
    --check) CHECK=1; shift;;
    --uninstall) UNINSTALL=1; shift;;
    --prefix|--dest) PREFIX="$2"; shift 2;;
    --prefix=*) PREFIX="${1#*=}"; shift;;
    --no-harness) NO_HARNESS=1; shift;;
    --provider) PROVIDER="$2"; shift 2;;
    --provider=*) PROVIDER="${1#*=}"; shift;;
    --skip-keys) SKIP_KEYS=1; shift;;
    --version|-v) python3 --version; exit 0;;
    -h|--help) sed -n '2,19p' "$0"; exit 0;;
    *) echo "[!] unknown option: $1" >&2; exit 2;;
  esac
done

{ [ $DRY_RUN -eq 1 ] && [ $CHECK -eq 1 ]; } && { echo "[!] --dry-run and --check are mutually exclusive"; exit 2; }
{ [ $UNINSTALL -eq 1 ] && [ $CHECK -eq 1 ]; } && { echo "[!] --uninstall and --check are mutually exclusive"; exit 2; }

# ---- locate a usable python3 (>= 3.10)
if ! command -v python3 >/dev/null 2>&1; then
  echo "[!] python3 not found — install Python 3.10+ and re-run." >&2; exit 1
fi
python3 - "$OS" <<'PY'
import sys
v = sys.version_info[:2]
if v < (3, 10):
    print(f"[!] python3 is {v[0]}.{v[1]}; Atlas requires 3.10+", file=sys.stderr)
    sys.exit(7)
print(f"→ python3 {v[0]}.{v[1]} ({sys.executable})")
PY

# ---- resolve install dir (default ~/atlas)
if [ -z "$PREFIX" ]; then PREFIX="$HOME/atlas"; fi
echo "→ target: $PREFIX  (os=$OS, repo=$REPO_ROOT, dry-run=$DRY_RUN)"

# ---- --check: read-only diagnostics on an existing install
if [ $CHECK -eq 1 ]; then
  PY="$PREFIX/.venv/bin/python"
  [ -x "$PY" ] || PY="python3"
  "$PY" - "$PREFIX" <<'PY'
import sys, pathlib, json
repo = pathlib.Path(sys.argv[1])
def ok(m, v, extra=""):
    print(f"  [{'OK' if v else 'FAIL'}] {m}" + (f"  {extra}" if extra else "")); return v
allok = True
# venv + deps
py = repo / ".venv" / "bin" / "python"
allok &= ok("venv present", py.exists(), str(py))
if py.exists():
    import subprocess
    r = subprocess.run([str(py), "-c", "import fastapi,uvicorn,httpx,orjson,rich"],
                       capture_output=True, text=True, timeout=30)
    allok &= ok("deps importable", r.returncode == 0, r.stderr.strip()[:120] or "fastapi/uvicorn/httpx/orjson/rich")
# proxy config (port is the single source of truth: proxy.config.LISTEN_PORT)
sys.path.insert(0, str(repo))
try:
    from proxy.config import LISTEN_HOST, LISTEN_PORT, PROVIDER
    allok &= ok("proxy config", True, f"{LISTEN_HOST}:{LISTEN_PORT} provider={PROVIDER}")
except Exception as e:
    allok &= ok("proxy config", False, str(e))
# key files
import itertools
files = [("openrouter", repo/"data/openrouter_data/openroute_keys.txt"),
         ("nvidia", repo/"data/nvda_data/keys.txt")]
for prov, f in files:
    n = sum(1 for ln in f.read_text().splitlines()) if f.exists() else 0
    n = sum(1 for ln in (f.read_text().splitlines() if f.exists() else []) if ln.strip() and not ln.startswith("#"))
    allok &= ok(f"upstream keys ({prov})", n > 0, f"{n} at {f.relative_to(repo)}")
# health (only if proxy is already running)
import urllib.request, urllib.error
try:
    port = LISTEN_PORT
except Exception:
    port = 8000
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
        allok &= ok("proxy /health", 200 <= resp.status < 300, f"HTTP {resp.status}")
except Exception as e:
    allok &= ok("proxy /health", False, str(e)[:80])
print("\n✓ All checks passed." if allok else "\n✘ Some checks failed — see above.")
sys.exit(0 if allok else 1)
PY
  exit $?
fi

# ---- --uninstall: stop runtime, remove venv + cli shim
if [ $UNINSTALL -eq 1 ]; then
  echo "Atlas uninstall  (target=$PREFIX, dry-run=$DRY_RUN)"
  if [ $DRY_RUN -eq 1 ]; then
    echo "DRY-RUN: would stop runtime, remove venv + cli shim at $PREFIX"
    exit 0
  fi
  PY="$PREFIX/.venv/bin/python"
  if [ -x "$PY" ]; then
    "$PY" - "$PREFIX" <<'PY'
import sys, pathlib
repo = pathlib.Path(sys.argv[1]); sys.path.insert(0, str(repo/"atlas"))
from bin import runtime as rt
from setup.core import venv_python, SERVICE_NAME
try:
    r = rt.get_runtime(repo, venv_python(repo), SERVICE_NAME)
    r.cleanup(); print("→ runtime stopped + config removed")
except Exception as e:
    print(f"→ runtime cleanup: {type(e).__name__}: {e}")
PY
  fi
  rm -rf "$PREFIX/.venv" && echo "→ removed venv"
  # cli shim — only remove if it points back at this prefix
  for shim in "$HOME/.local/bin/atlas" "$HOME/.local/bin/atlas.cmd"; do
    if [ -e "$shim" ]; then
      tgt=$(readlink -f "$shim" 2>/dev/null || echo "$shim")
      case "$tgt" in *"$PREFIX"*) rm -f "$shim"; echo "→ removed $shim";; *) echo "→ left $shim (points elsewhere)";; esac
    fi
  done
  echo "Done. (Kept data/ key files — rm -rf $PREFIX to delete everything.)"
  exit 0
fi

# ---- normal install
# Copy the repo to the target unless installing in place (PREFIX == REPO_ROOT).
if [ "$PREFIX" != "$REPO_ROOT" ]; then
  if [ ! -d "$PREFIX/proxy" ]; then
    echo "→ copying source into $PREFIX (leaving $REPO_ROOT untouched)"
    python3 - "$REPO_ROOT" "$PREFIX" <<'PY'
import sys, pathlib
sys.path.insert(0, sys.argv[1])
from setup.core import copy_source_to
copy_source_to(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
PY
  else
    echo "→ $PREFIX already has the proxy — merging (rerun-safe)"
    python3 - "$REPO_ROOT" "$PREFIX" <<'PY'
import sys, pathlib
sys.path.insert(0, sys.argv[1])
from setup.core import copy_source_to
copy_source_to(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
PY
  fi
fi

VENVPY="$PREFIX/.venv/bin/python"
if [ ! -x "$VENVPY" ]; then
  echo "→ creating virtualenv at $PREFIX/.venv"
  [ $DRY_RUN -eq 1 ] || python3 -m venv "$PREFIX/.venv"
  [ -x "$VENVPY" ] || { echo "[!] venv creation failed (need python3-venv?)"; exit 1; }
fi

if [ $DRY_RUN -eq 0 ]; then
  echo "→ installing dependencies"
  "$VENVPY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
  "$VENVPY" -m pip install --quiet -r "$PREFIX/requirements.txt" || { echo "[!] pip install failed"; exit 1; }
fi

# Interactive harness + keys + verify + launch (venv python so Rich is available).
cd "$PREFIX"
WFLAGS=( --repo "$PREFIX" )
[ $DRY_RUN -eq 1 ] && WFLAGS+=( --dry-run )
[ $NO_HARNESS -eq 1 ] && WFLAGS+=( --no-harness )
[ -n "$PROVIDER" ] && WFLAGS+=( --provider "$PROVIDER" )
[ $SKIP_KEYS -eq 1 ] && WFLAGS+=( --skip-keys )
echo "→ launching wizard"
# Regression guard: prove the wizard module is runnable as a script before
# handing off. A missing `if __name__ == "__main__"` block would otherwise make
# `python -m setup.wizard` exit silently with nothing installed.
"$VENVPY" -m setup.wizard --help >/dev/null 2>&1 || {
    echo "[!] setup/wizard.py has no usable entry point (missing __main__) — aborting" >&2
    exit 1
}
exec "$VENVPY" -m setup.wizard "${WFLAGS[@]}"
