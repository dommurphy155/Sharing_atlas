<#
.SYNOPSIS
    Atlas proxy installer — Windows (PowerShell) first-run bootstrap.

.DESCRIPTION
    Bootstraps a self-contained install at -Prefix (default ~/atlas):
    system Python 3.10+ -> venv -> deps -> interactive wizard (setup/wizard.py).

.EXAMPLE
    .\install.ps1
    .\install.ps1 -Prefix C:\tools\atlas
    .\install.ps1 -Check
    .\install.ps1 -Uninstall
    .\install.ps1 -DryRun -NoHarness -Provider openrouter -SkipKeys
#>
[CmdletBinding()]
param(
    [string]$Prefix = "$env:USERPROFILE\atlas",
    [string]$Provider,
    [switch]$DryRun,
    [switch]$Check,
    [switch]$Uninstall,
    [switch]$NoHarness,
    [switch]$SkipKeys
)

$ErrorActionPreference = "Stop"
[Environment]::SetEnvironmentVariable("PYTHONDONTWRITEBYTECODE", "1", "Process")
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---- locate Python 3.10+
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { $py = (Get-Command python3 -ErrorAction SilentlyContinue) }
if (-not $py) { Write-Error "python/python3 not found — install Python 3.10+ and re-run."; exit 1 }
$ver = & $py.Python -c "import sys;print('%d.%d'%sys.version_info[:2])"
if ($ver -as [version] -lt [version]"3.10") { Write-Error "Python $ver too old — need 3.10+"; exit 7 }
Write-Host "-> python $ver ($($py.Python))"

$VenvPython = Join-Path $Prefix ".venv\Scripts\python.exe"

# ---- --Check
if ($Check) {
    $runner = if (Test-Path $VenvPython) { $VenvPython } else { $py.Python }
    & $runner - "$Prefix" @"
import sys, pathlib, subprocess, urllib.request
repo = pathlib.Path(sys.argv[1])
def ok(m,v,extra=""):
    print(f"  [{'OK' if v else 'FAIL'}] {m}" + (f"  {extra}" if extra else "")); return v
allok=True
ok("venv present", (repo/".venv").exists())
if (repo/".venv").exists():
    r = subprocess.run(["$runner","-c","import fastapi,uvicorn,httpx,orjson,rich"], capture_output=True, text=True, timeout=30)
    allok &= ok("deps importable", r.returncode==0, r.stderr.strip()[:120] or "fastapi/uvicorn/httpx/orjson/rich")
sys.path.insert(0, str(repo))
try:
    from proxy.config import LISTEN_HOST, LISTEN_PORT, PROVIDER
    allok &= ok("proxy config", True, f"{LISTEN_HOST}:{LISTEN_PORT} provider={PROVIDER}")
except Exception as e:
    allok &= ok("proxy config", False, str(e))
for prov,f in [("openrouter",repo/"data/openrouter_data/openroute_keys.txt"),("huggingface",repo/"data/huggingface_data/hf_keys.txt"),("nvidia",repo/"data/nvda_data/keys.txt")]:
    n=0
    if (f).exists():
        n=sum(1 for ln in f.read_text().splitlines() if ln.strip() and not ln.startswith("#"))
    allok &= ok(f"upstream keys ({prov})", n>0, f"{n} at {f.relative_to(repo)}")
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{LISTEN_PORT}/health", timeout=2) as r:
        allok &= ok("proxy /health", 200<=r.status<300, f"HTTP {r.status}")
except Exception as e:
    allok &= ok("proxy /health", False, str(e)[:80])
print("\\nAll checks passed." if allok else "\\nSome checks failed")
sys.exit(0 if allok else 1)
"@
    exit $LASTEXITCODE
}

# ---- --Uninstall
if ($Uninstall) {
    Write-Host "Atlas uninstall  (target=$Prefix, dry-run=$DryRun)"
    if ($DryRun) { Write-Host "DRY-RUN: would stop runtime, remove venv + cli shim"; exit 0 }
    if (Test-Path $VenvPython) {
        & $VenvPython - "$Prefix" @"
import sys, pathlib
repo = pathlib.Path(sys.argv[1]); import os
try:
    sys.path.insert(0, str(repo/"atlas")); sys.path.insert(0, str(repo/"setup"))
    from bin import runtime as rt
    from setup.core import venv_python, SERVICE_NAME
    r = rt.get_runtime(repo, venv_python(repo), SERVICE_NAME)
    r.cleanup(); print("-> runtime stopped + config removed")
except Exception as e:
    print(f"-> runtime cleanup: {type(e).__name__}: {e}")
"@
    }
    if (Test-Path "$Prefix\.venv") { Remove-Item -Recurse -Force "$Prefix\.venv"; Write-Host "-> removed venv" }
    $shim = "$env:USERPROFILE\.local\bin\atlas.cmd"
    if (Test-Path $shim) {
        if ((Get-Item $shim).Target -match [regex]::Escape($Prefix)) { Remove-Item $shim; Write-Host "-> removed $shim" }
    }
    Write-Host "Done. (Kept data\ key files — Remove-Item -Recurse $Prefix to delete everything.)"
    exit 0
}

# ---- normal install
if ($Prefix -ne $RepoRoot) {
    $needCopy = -not (Test-Path "$Prefix\proxy\main.py")
    # Copy using Python's shutil.copytree (ignore patterns match core._COPY_IGNORE)
    $py.Python - "$RepoRoot" "$Prefix" @"
import sys, pathlib
sys.path.insert(0, sys.argv[1])
from setup.core import copy_source_to
copy_source_to(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
"@
    if ($needCopy) { Write-Host "-> copied source into $Prefix" } else { Write-Host "-> $Prefix already has proxy — merged (rerun-safe)" }
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "-> creating virtualenv at $Prefix\.venv"
    if (-not $DryRun) { & $py.Python -m venv "$Prefix\.venv" }
    if (-not (Test-Path $VenvPython)) { Write-Error "venv creation failed"; exit 1 }
}

if (-not $DryRun) {
    Write-Host "-> installing dependencies"
    & $VenvPython -m pip install --quiet --upgrade pip
    & $VenvPython -m pip install --quiet -r "$Prefix\requirements.txt"
    if ($LASTEXITCODE) { Write-Error "pip install failed"; exit 1 }
}

$wflags = @( "-Repo", $Prefix )
if ($DryRun) { $wflags += "-DryRun" }
if ($NoHarness) { $wflags += "-NoHarness" }
if ($Provider) { $wflags += "-Provider", $Provider }
if ($SkipKeys) { $wflags += "-SkipKeys" }

Write-Host "-> launching wizard"
Push-Location $Prefix
try {
    & $VenvPython -m setup.wizard @wflags
    exit $LASTEXITCODE
} finally { Pop-Location }
