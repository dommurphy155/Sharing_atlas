"""Atlas cross-platform installer package.

Shared utilities live here (stdlib-only so the bootstrap path can run before any
third-party deps are installed).

Setup flow (one command):
    1. Bootstrap   — detect OS, check Python >= 3.10, create venv, install deps
    2. Collect keys — prompt for OpenRouter API key(s), save to key file
    3. Start proxy — launch proxy.main detached, verify /health + keys loaded
    4. Exit        — report success, tell user how to stop

No harness configuration. No auto-start registration. No CLI shims.
Cross-platform: Linux, macOS, Windows.

Layout:
    repo/
      proxy/              # the proxy package (importable once deps are installed)
      atlas/bin/atlas     # the post-install CLI (atlas start/stop/status/etc.)
      setup/              # this installer package
      install.sh          # Linux/macOS entry
      install.ps1         # Windows PowerShell entry
      requirements.txt
"""
from __future__ import annotations
