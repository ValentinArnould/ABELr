# bootstrap.ps1 -- builds the plugin's venv + installs Python dependencies.
#
# Launched automatically by launch.ps1 on the very first startup (venv missing under
# app/.venv). Can also be re-run by hand (idempotent) to repair a broken install.
# $PSScriptRoot = ABELr.lrplugin/ (the plugin embeds everything: app/,
# launch.ps1, bootstrap.ps1 -- copying just this folder is enough on another machine).
#
# GPU detection: torch/torchvision are installed as CUDA (cu124) if an NVIDIA GPU is
# detected (nvidia-smi present), otherwise as a CPU build (PyPI). "GPU first,
# CPU fallback" policy -- see app/core/gpu.py.

$ErrorActionPreference = 'Stop'
$root    = $PSScriptRoot
$appDir  = Join-Path $root 'app'
$venvDir = Join-Path $appDir '.venv'
$venvPy  = Join-Path $venvDir 'Scripts\python.exe'
$reqFile = Join-Path $appDir 'requirements.txt'

Write-Host '[ABELr] Bootstrap -- checking Python...'

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Error "Python not found in PATH. Install Python 3.11+ (https://www.python.org/downloads/), check `"Add python.exe to PATH`", then relaunch."
    exit 1
}

try {
    $verOutput = & python --version 2>&1
} catch {
    $verOutput = ''
}
if ($verOutput -match 'Python (\d+)\.(\d+)') {
    $maj = [int]$Matches[1]
    $min = [int]$Matches[2]
    if ($maj -lt 3 -or ($maj -eq 3 -and $min -lt 11)) {
        Write-Error "Python $maj.$min detected -- 3.11+ required for ABELr."
        exit 1
    }
    Write-Host "[ABELr] Python $maj.$min OK."
} else {
    Write-Warning "Unrecognized Python version (`"$verOutput`") -- continuing anyway."
}

if (-not (Test-Path $venvPy)) {
    Write-Host "[ABELr] Creating venv: $venvDir"
    python -m venv $venvDir
    if (-not (Test-Path $venvPy)) {
        Write-Error "Failed to create the venv (python.exe missing after `"python -m venv`")."
        exit 1
    }
} else {
    Write-Host '[ABELr] venv already present -- reusing.'
}

Write-Host '[ABELr] Updating pip...'
& $venvPy -m pip install --upgrade pip

# NVIDIA GPU detection (nvidia-smi present = driver installed and working).
$hasNvidia = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)

if ($hasNvidia) {
    Write-Host '[ABELr] NVIDIA GPU detected -- installing torch CUDA (cu124)...'
    & $venvPy -m pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
} else {
    Write-Host '[ABELr] No NVIDIA GPU detected -- installing torch CPU (fallback).'
    & $venvPy -m pip install torch==2.6.0 torchvision==0.21.0
}
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to install torch/torchvision (code $LASTEXITCODE)."
    exit 1
}

Write-Host '[ABELr] Installing other dependencies (requirements.txt)...'
& $venvPy -m pip install -r $reqFile
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to install requirements.txt (code $LASTEXITCODE)."
    exit 1
}

# exiftool: external non-pip binary (Sony CreativeStyle creator profile). Looks
# first for a local bundle (bin/exiftool.exe), then the system PATH; its absence is
# handled as non-blocking by app/core/exif_profile.py (just a missing profile).
$exiftoolBundled = Join-Path $root 'bin\exiftool.exe'
$exiftoolInPath  = Get-Command exiftool -ErrorAction SilentlyContinue
if (-not $exiftoolInPath -and -not (Test-Path $exiftoolBundled)) {
    Write-Warning "exiftool not found (neither PATH nor bin\exiftool.exe) -- the Sony creator profile will be unavailable (non-blocking). Install from https://exiftool.org if needed."
}

Write-Host '[ABELr] Bootstrap complete.'
