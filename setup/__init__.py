"""Atlas cross-platform installer package.

Shared utilities live here (stdlib-only so the bootstrap path can run before any
third-party deps are installed). OS-specific behaviour lives in
``linux_setup.py``, ``mac_setup.py`` and ``windows_setup.py``.

Layout:
    repo/
      proxy/              # the proxy package (importable once deps are installed)
      atlas/bin/atlas     # the post-install CLI shim points at the repo venv
      setup/              # this installer package
      install.sh          # Linux/macOS entry
      install.ps1         # Windows PowerShell entry
      requirements.txt
"""
from __future__ import annotations
