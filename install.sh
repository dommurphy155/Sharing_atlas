#!/usr/bin/env bash
# Atlas proxy installer — Linux & macOS.
#
# One-command setup: bootstrap → enter OpenRouter keys → start proxy → verify → exit.
#
# Usage:
#   install.sh                 Interactive install
#   install.sh --dry-run       Bootstrap only (venv + deps), no wizard
#   install.sh --help          Show this help
#
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$OS" in
  linux*) OS=linux ;;
  darwin*) OS=macos ;;
  *) echo "[!] unsupported OS: $OS" >&2; exit 1 ;;
esac

DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --help|-h) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "[!] unknown option: $1" >&2; exit 2 ;;
  esac
done

# ---- Check Python 3.10+
if ! command -v python3 >/dev/null 2>&1; then
  echo "[!] python3 not found — install Python 3.10+ and re-run." >&2; exit 1
fi
python3 - <<'PY'
import sys
v = sys.version_info[:2]
if v < (3, 10):
    print(f"[!] python3 is {v[0]}.{v[1]}; Atlas requires 3.10+", file=sys.stderr)
    sys.exit(7)
print(f"→ python3 {v[0]}.{v[1]}")
PY

echo "→ repo: $REPO_ROOT  (os=$OS)"

# ---- Dry-run: bootstrap only
if [ $DRY_RUN -eq 1 ]; then
  echo "DRY-RUN: bootstrap only (venv + deps), no wizard"
  if [ ! -x "$REPO_ROOT/.venv/bin/python" ]; then
    python3 -m venv "$REPO_ROOT/.venv"
  fi
  "$REPO_ROOT/.venv/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
  "$REPO_ROOT/.venv/bin/python" -m pip install --quiet -r "$REPO_ROOT/requirements.txt" || { echo "[!] pip install failed"; exit 1; }
  exit 0
fi

# ---- Create venv
if [ ! -x "$REPO_ROOT/.venv/bin/python" ]; then
  echo "→ creating virtualenv"
  python3 -m venv "$REPO_ROOT/.venv"
  [ -x "$REPO_ROOT/.venv/bin/python" ] || { echo "[!] venv creation failed"; exit 1; }
fi

# ---- Install dependencies
echo "→ installing dependencies"
"$REPO_ROOT/.venv/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
"$REPO_ROOT/.venv/bin/python" -m pip install --quiet -r "$REPO_ROOT/requirements.txt" || { echo "[!] pip install failed"; exit 1; }

# ---- Run the wizard
cd "$REPO_ROOT"
exec "$REPO_ROOT/.venv/bin/python" -m setup.wizard
