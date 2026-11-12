
<#
.SYNOPSIS
    Atlas proxy installer - Windows.
.DESCRIPTION
    Bootstrap dependencies, run the setup wizard, and return its exit code.
.EXAMPLE
    .\install.ps1
    .\install.ps1 -DryRun
#>

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Check,
    [switch]$Uninstall,
    [switch]$Install
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$RepoRoot = $PSScriptRoot
$VenvPath = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
$Requirements = Join-Path $RepoRoot "requirements.txt"

function Stop-Installer {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,

        [int]$Code = 1
    )

    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit $Code
}

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$FailureMessage
    )

    & $Executable @Arguments

    if ($LASTEXITCODE -ne 0) {
        Stop-Installer "$FailureMessage (exit code: $LASTEXITCODE)" $LASTEXITCODE
    }
}

# ---- Locate a working Python 3.10+ installation
function Find-WorkingPython {
    $Candidates = @()

    foreach ($CommandName in @("python", "python3", "py")) {
        $Found = @(Get-Command $CommandName -CommandType Application -All -ErrorAction SilentlyContinue)

        foreach ($Item in $Found) {
            $CandidatePath = $Item.Source
            if (-not $CandidatePath) { $CandidatePath = $Item.Path }
            if ($CandidatePath) {
                $Candidates += [pscustomobject]@{
                    Path = $CandidatePath
                    Launcher = ($CommandName -eq "py")
                }
            }
        }
    }

    # Check common per-user installation locations, even if PATH is stale.
    foreach ($Version in @("313", "312", "311", "310")) {
        $KnownPath = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$Version\python.exe"
        if (Test-Path $KnownPath -PathType Leaf) {
            $Candidates += [pscustomobject]@{
                Path = $KnownPath
                Launcher = $false
            }
        }
    }

    $Seen = @{}
    foreach ($Candidate in $Candidates) {
        $CandidatePath = $Candidate.Path

        # Ignore Windows Store execution aliases. They are not Python installations.
        if ($CandidatePath -match '(?i)\\Microsoft\\WindowsApps\\') { continue }
        if ($Seen.ContainsKey($CandidatePath)) { continue }
        $Seen[$CandidatePath] = $true

        try {
            if ($Candidate.Launcher) {
                & $CandidatePath -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" *> $null
            }
            else {
                & $CandidatePath -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" *> $null
            }

            $CandidateExitCode = $LASTEXITCODE
            if ($CandidateExitCode -eq 0) {
                return $Candidate
            }
        }
        catch {
            # Broken aliases and stale PATH entries are skipped.
        }
    }

    return $null
}

$PythonFound = Find-WorkingPython

if (-not $PythonFound) {
    Write-Host "-> Python 3.10+ was not found. Checking for WinGet..."

    $Winget = Get-Command winget -CommandType Application -ErrorAction SilentlyContinue

    if ($Winget) {
        Write-Host "-> Installing Python 3.12 for the current Windows user..."
        try {
            & $Winget.Source install --id Python.Python.3.12 --exact --scope user --silent --accept-package-agreements --accept-source-agreements
            $WingetExitCode = $LASTEXITCODE
        }
        catch {
            $WingetExitCode = 1
            Write-Host "WinGet installation error: $_" -ForegroundColor Yellow
        }

        # Refresh PATH from Windows user and machine environment settings.
        $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $MachinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
        $env:Path = "$UserPath;$MachinePath;$env:Path"

        $PythonFound = Find-WorkingPython

        if (-not $PythonFound) {
            Write-Host "WinGet did not produce a usable Python installation (exit code: $WingetExitCode)." -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "WinGet is not available on this machine." -ForegroundColor Yellow
    }
}

if (-not $PythonFound) {
    Write-Host ""
    Write-Host "Python 3.10 or newer is required, but automatic installation was unsuccessful." -ForegroundColor Red
    Write-Host "Install Python 3.12 from the official download page:"
    Write-Host "https://www.python.org/downloads/windows/"
    Write-Host ""
    Write-Host "During installation, enable 'Add python.exe to PATH'."
    Write-Host "Then close PowerShell, reopen it, and run .\install.ps1 again."
    Stop-Installer "Python bootstrap could not find or install a working interpreter." 1
}

$PythonCommand = $PythonFound.Path
$PythonLauncher = $PythonFound.Launcher

# ---- Read and validate Python version
$VersionOutput = & $PythonCommand -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1

if ($LASTEXITCODE -ne 0 -or "$VersionOutput" -notmatch '^\d+\.\d+$') {
    # If the selected command was the Python launcher, retry with -3.
    $VersionOutput = & $PythonCommand -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1

    if ($LASTEXITCODE -ne 0 -or "$VersionOutput" -notmatch '^\d+\.\d+$') {
        Stop-Installer "Unable to determine the Python version." 1
    }

    $PythonLauncher = $true
}
else {
    $PythonLauncher = $false
}

$PythonVersion = [version]("$VersionOutput".Trim())

if ($PythonVersion -lt [version]"3.10") {
    Stop-Installer "Python $PythonVersion is too old. Python 3.10+ is required." 7
}

Write-Host "-> Python $PythonVersion"
Write-Host "-> Repository: $RepoRoot"

# ---- Home-directory sanity check
# The atlas shim lives under the current user's profile. If PowerShell's $HOME
# and %USERPROFILE% disagree, the installer would otherwise write the shim to
# one place and look for it in another. Surface the mismatch instead of failing
# later with a confusing "launcher was not created".
$ProfileMismatch = -not [string]::Equals(
    ($HOME | ForEach-Object { $_.TrimEnd("\") }),
    ($env:USERPROFILE | ForEach-Object { $_.TrimEnd("\") }),
    [StringComparison]::OrdinalIgnoreCase
)
if ($ProfileMismatch) {
    Write-Host "-> home directories differ: `$HOME='$HOME' USERPROFILE='$env:USERPROFILE'" -ForegroundColor Yellow
    Write-Host "   the atlas shim will be installed under %USERPROFILE%" -ForegroundColor Yellow
}

if (-not (Test-Path $Requirements -PathType Leaf)) {
    Stop-Installer "requirements.txt not found: $Requirements" 1
}

# Build arguments for the selected interpreter.
$PythonArgs = @()
if ($PythonLauncher) {
    $PythonArgs += "-3"
}

# ---- Dry run: bootstrap only
if ($DryRun) {
    Write-Host "DRY-RUN: bootstrap dependencies only; wizard will not run."

    if (-not (Test-Path $VenvPython -PathType Leaf)) {
        Write-Host "-> creating virtual environment"
        Invoke-Python $PythonCommand ($PythonArgs + @("-m", "venv", $VenvPath)) "Virtual environment creation failed"
    }

    Invoke-Python $VenvPython @("-m", "pip", "install", "--quiet", "--upgrade", "pip") "pip upgrade failed"
    Invoke-Python $VenvPython @("-m", "pip", "install", "--quiet", "-r", $Requirements) "Dependency installation failed"

    Write-Host "DRY-RUN: bootstrap completed successfully."
    exit 0
}

# ---- -Check: run diagnostics
if ($Check) {
    Write-Host "-> running diagnostics"
    if (Test-Path $VenvPython -PathType Leaf) {
        Push-Location $RepoRoot
        try {
            $CheckCode = 'import sys; sys.path.insert(0, "."); from setup import core; ok, msg = core._os_module().verify_startup(core.repo_root()); print(msg); sys.exit(0 if ok else 1)'
            & $VenvPython -c $CheckCode
            $ExitCode = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        exit $ExitCode
    } else {
        Write-Host "ERR: venv not found - run install first" -ForegroundColor Red
        exit 1
    }
}

# ---- -Uninstall: stop proxy and remove runtime config
if ($Uninstall) {
    Write-Host "-> stopping proxy and cleaning up"
    if (Test-Path $VenvPython -PathType Leaf) {
        Push-Location $RepoRoot
        try {
            $UninstallCode = 'import sys; sys.path.insert(0, "."); from setup import core; ok, msg = core._os_module().uninstall_runtime(core.repo_root()); print(msg); sys.exit(0 if ok else 1)'
            & $VenvPython -c $UninstallCode
            if ($LASTEXITCODE -ne 0) { Stop-Installer "Runtime uninstall failed." $LASTEXITCODE }
        } finally {
            Pop-Location
        }
    }
    Write-Host "-> uninstall complete"
    exit 0
}

# ---- Create virtual environment if missing
if (-not (Test-Path $VenvPython -PathType Leaf)) {
    Write-Host "-> creating virtual environment"

    Invoke-Python $PythonCommand ($PythonArgs + @("-m", "venv", $VenvPath)) "Virtual environment creation failed"

    if (-not (Test-Path $VenvPython -PathType Leaf)) {
        Stop-Installer "Virtual environment creation finished, but its Python executable is missing." 1
    }
}

# ---- Verify the existing or newly created virtual environment
$VenvVersionOutput = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1

if ($LASTEXITCODE -ne 0 -or "$VenvVersionOutput" -notmatch '^\d+\.\d+$') {
    Stop-Installer "The virtual environment is invalid or its Python executable cannot run. Inspect .venv before repairing it." 1
}

$VenvVersion = [version]("$VenvVersionOutput".Trim())

if ($VenvVersion -lt [version]"3.10") {
    Stop-Installer "The existing virtual environment uses Python $VenvVersion; Python 3.10+ is required. Existing files were preserved." 7
}

Write-Host "-> virtual environment Python $VenvVersion"

# ---- Install dependencies
Write-Host "-> upgrading pip"
Invoke-Python $VenvPython @("-m", "pip", "install", "--quiet", "--upgrade", "pip") "pip upgrade failed"

Write-Host "-> installing dependencies"
Invoke-Python $VenvPython @("-m", "pip", "install", "--quiet", "-r", $Requirements) "Dependency installation failed"

# ---- Verify the setup wizard exists
$WizardFile = Join-Path $RepoRoot "setup\wizard.py"

if (-not (Test-Path $WizardFile -PathType Leaf)) {
    Stop-Installer "Setup wizard not found: $WizardFile" 1
}

# ---- Run the wizard and preserve its exit code
Write-Host "-> starting setup wizard"

Push-Location $RepoRoot

try {
    & $VenvPython -m setup.wizard
    $WizardExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($WizardExitCode -ne 0) {
    Stop-Installer "Setup wizard failed." $WizardExitCode
}

# ---- Refresh and verify the Atlas CLI in this PowerShell session
# The shim is written under the *current user's* profile. $HOME can diverge from
# %USERPROFILE% (domain profiles, OneDrive-redirected folders, Git Bash), so
# resolve both and prefer whichever actually holds the launcher.
$AtlasBin = $null
foreach ($HomeDir in @($HOME, $env:USERPROFILE) | Select-Object -Unique) {
    if (-not $HomeDir) { continue }
    $CandidateBin = Join-Path $HomeDir ".local\bin"
    if (Test-Path (Join-Path $CandidateBin "atlas.cmd") -PathType Leaf) {
        $AtlasBin = $CandidateBin
        break
    }
}
if (-not $AtlasBin) {
    $AtlasBin = Join-Path $env:USERPROFILE ".local\bin"
}
$AtlasShim = Join-Path $AtlasBin "atlas.cmd"
if (-not (Test-Path $AtlasShim -PathType Leaf)) {
    $Tried = (@($HOME, $env:USERPROFILE) | Select-Object -Unique | ForEach-Object { Join-Path $_ ".local\bin\atlas.cmd" }) -join ", "
    Stop-Installer "Atlas CLI launcher was not created. Looked for: $Tried" 1
}
if ($ProfileMismatch) {
    Write-Host "-> using launcher at $AtlasShim (`$HOME and USERPROFILE differ)" -ForegroundColor Yellow
}

$AtlasBin = Split-Path $AtlasShim -Parent
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$PathEntries = @($UserPath -split ";" | Where-Object { $_.Trim() })
if (-not ($PathEntries | Where-Object {
    [string]::Equals($_.Trim().TrimEnd("\"), $AtlasBin.TrimEnd("\"), [StringComparison]::OrdinalIgnoreCase)
})) {
    $UpdatedUserPath = (@($PathEntries) + $AtlasBin) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $UpdatedUserPath, "User")
}
if (-not (($env:Path -split ";" | Where-Object {
    [string]::Equals($_.Trim().TrimEnd("\"), $AtlasBin.TrimEnd("\"), [StringComparison]::OrdinalIgnoreCase)
}))) {
    $env:Path = "$AtlasBin;$env:Path"
}

if (-not (Get-Command atlas -ErrorAction SilentlyContinue)) {
    Stop-Installer "Atlas launcher exists but 'atlas' cannot be resolved from PATH." 1
}

# Verify the launcher actually runs, not just that it resolves. A shim that
# cannot execute its interpreter is worth surfacing now rather than leaving the
# user with a confusing "python: can't open file" later. This is a warning, not
# a failure: the proxy is already up and healthy, and a broken convenience
# command must not fail an otherwise working install.
& $AtlasShim --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARN: Atlas launcher at $AtlasShim resolved but failed to run (exit code: $LASTEXITCODE)." -ForegroundColor Yellow
    Write-Host "      The proxy is running; only the 'atlas' shortcut is affected." -ForegroundColor Yellow
    Write-Host "      Invoke the CLI directly: `"$VenvPython`" `"$RepoRoot\atlas\bin\atlas`" --help" -ForegroundColor Yellow
}
else {
    Write-Host "-> Atlas CLI verified: $AtlasShim (runnable, version check passed)"
}

Write-Host "-> installer finished successfully"
exit 0
