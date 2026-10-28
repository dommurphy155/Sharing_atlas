<#
.SYNOPSIS
    Atlas proxy installer — Windows.
.DESCRIPTION
    One-command setup: bootstrap → enter OpenRouter keys → start proxy → verify → exit.
.EXAMPLE
    .\install.ps1
    .\install.ps1 -DryRun
#>

[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---- Check Python 3.10+
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Error "python/python3 not found — install Python 3.10+ and re-run."
    exit 1
}
$ver = & $py.Python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($ver -as [version] -lt [version]"3.10") {
    Write-Error "Python $ver too old — need 3.10+"
    exit 7
}
Write-Host "-> python $ver"

Write-Host "-> repo: $RepoRoot"

# ---- Dry-run: bootstrap only
if ($DryRun) {
    Write-Host "DRY-RUN: bootstrap only (venv + deps), no wizard"
    $VenvPython = "$RepoRoot\.venv\Scripts\python.exe"
    if (-not (Test-Path $VenvPython)) {
        & $py.Python -m venv "$RepoRoot\.venv"
    }
    & $VenvPython -m pip install --quiet --upgrade pip
    & $VenvPython -m pip install --quiet -r "$RepoRoot\requirements.txt"
    if ($LASTEXITCODE) { Write-Error "pip install failed"; exit 1 }
    exit 0
}

# ---- Create venv
$VenvPython = "$RepoRoot\.venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "→ creating virtualenv"
    & $py.Python -m venv "$RepoRoot\.venv"
    if (-not (Test-Path $VenvPython)) { Write-Error "venv creation failed"; exit 1 }
}

# ---- Install dependencies
Write-Host "→ installing dependencies"
& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet -r "$RepoRoot\requirements.txt"
if ($LASTEXITCODE) { Write-Error "pip install failed"; exit 1 }

# ---- Run the wizard
Push-Location $RepoRoot
try {
    & $VenvPython -m setup.wizard
    exit $LASTEXITCODE
} finally { Pop-Location }
