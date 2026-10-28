
<#
.SYNOPSIS
    Atlas proxy installer — Windows.
.DESCRIPTION
    Bootstrap dependencies, run the setup wizard, and return its exit code.
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

# ---- Locate a working Python 3.10+
$PythonCommand = $null

foreach ($CommandName in @("python", "python3", "py")) {
    $Candidates = @(Get-Command $CommandName -CommandType Application -ErrorAction SilentlyContinue)

    foreach ($Candidate in $Candidates) {
        $CandidatePath = $Candidate.Source

        if (-not $CandidatePath) {
            $CandidatePath = $Candidate.Path
        }

        if (-not $CandidatePath) {
            continue
        }

        # The Python launcher needs a version selector.
        if ($CommandName -eq "py") {
            & $CandidatePath -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        }
        else {
            & $CandidatePath -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        }

        if ($LASTEXITCODE -eq 0) {
            $PythonCommand = $CandidatePath
            break
        }
    }

    if ($PythonCommand) {
        break
    }
}

if (-not $PythonCommand) {
    Stop-Installer "No working Python 3.10+ installation found. Install Python 3.10 or newer, then reopen PowerShell." 1
}

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

Write-Host "-> installer finished successfully"
exit 0
