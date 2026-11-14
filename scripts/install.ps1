<#
.SYNOPSIS
    SUTRA universal installer for Windows.

.DESCRIPTION
    Creates an isolated virtual environment, installs the `sutra-agent`
    distribution into it, and verifies the CLI. Safe to re-run: an existing
    environment is reused and the package is upgraded in place.

        irm https://raw.githubusercontent.com/g0GobliN/sutra/main/scripts/install.ps1 | iex
        .\scripts\install.ps1 -DryRun

.PARAMETER DryRun
    Print the actions without writing anything.

.PARAMETER Venv
    Virtual environment location. Defaults to SUTRA_VENV or %USERPROFILE%\.sutra-venv.

.PARAMETER Package
    Distribution to install. Defaults to SUTRA_PACKAGE or sutra-agent.
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$Venv = $(if ($env:SUTRA_VENV) { $env:SUTRA_VENV } else { Join-Path $env:USERPROFILE '.sutra-venv' }),
    [string]$Package = $(if ($env:SUTRA_PACKAGE) { $env:SUTRA_PACKAGE } else { 'sutra-agent' })
)

$ErrorActionPreference = 'Stop'

function Get-PythonVersion {
    param([string]$Exe, [string[]]$Prefix)

    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) {
        return $null
    }
    try {
        $version = & $Exe @Prefix -c "import sys; print('%d.%d' % sys.version_info[:2]); raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version) {
            return ($version | Select-Object -First 1)
        }
    } catch {
        return $null
    }
    return $null
}

$candidates = @(
    @{ Exe = 'python';  Prefix = @() },
    @{ Exe = 'py';      Prefix = @('-3.14') },
    @{ Exe = 'py';      Prefix = @('-3.13') },
    @{ Exe = 'py';      Prefix = @('-3.12') },
    @{ Exe = 'py';      Prefix = @('-3.11') }
)

$python = $null
$pythonPrefix = @()
$pythonVersion = $null

if ($env:SUTRA_PYTHON) {
    $pythonVersion = Get-PythonVersion -Exe $env:SUTRA_PYTHON -Prefix @()
    if (-not $pythonVersion) {
        Write-Error "SUTRA_PYTHON=$($env:SUTRA_PYTHON) is not Python 3.11 or newer."
        exit 1
    }
    $python = $env:SUTRA_PYTHON
} else {
    foreach ($candidate in $candidates) {
        $pythonVersion = Get-PythonVersion -Exe $candidate.Exe -Prefix $candidate.Prefix
        if ($pythonVersion) {
            $python = $candidate.Exe
            $pythonPrefix = $candidate.Prefix
            break
        }
    }
}

if (-not $python) {
    Write-Error @"
Python 3.11 or newer is required and none was found.

Install it first, then re-run this script:
  winget install Python.Python.3.13
"@
    exit 1
}

$prefixText = if ($pythonPrefix.Count -gt 0) { ' ' + ($pythonPrefix -join ' ') } else { '' }
Write-Host "sutra: using Python $pythonVersion ($python$prefixText)"
Write-Host "sutra: virtual environment: $Venv"

if ($DryRun) {
    if (Test-Path $Venv) {
        Write-Host "sutra: would reuse the existing environment"
    } else {
        Write-Host "sutra: would create the environment"
    }
    Write-Host "sutra: would install or upgrade $Package"
    Write-Host "sutra: would verify $(Join-Path $Venv 'Scripts\sutra.exe') --version"
    Write-Host "sutra: dry run; nothing was written"
    exit 0
}

if (Test-Path $Venv) {
    Write-Host "sutra: reusing the existing environment"
} else {
    & $python @pythonPrefix -m venv $Venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$venvPython = Join-Path $Venv 'Scripts\python.exe'
$venvSutra = Join-Path $Venv 'Scripts\sutra.exe'

& $venvPython -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $venvPython -m pip install --quiet --upgrade $Package
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $venvSutra --version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$scripts = Join-Path $Venv 'Scripts'
Write-Host ""
Write-Host "sutra: installed."
Write-Host ""
Write-Host "Add it to this session:"
Write-Host "  `$env:Path = `"$scripts;`$env:Path`""
Write-Host ""
Write-Host "Add it permanently (user scope):"
Write-Host "  [Environment]::SetEnvironmentVariable('Path', `"$scripts;`" + [Environment]::GetEnvironmentVariable('Path', 'User'), 'User')"
Write-Host ""
Write-Host "Then wire it into the agents you have installed:"
Write-Host "  sutra install"
Write-Host ""
Write-Host "Verify the integrations with:"
Write-Host "  sutra doctor"
