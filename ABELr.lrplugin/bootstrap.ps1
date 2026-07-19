# bootstrap.ps1 — construit le venv du plugin + installe les dependances Python.
#
# Lance automatiquement par launch.ps1 au tout premier demarrage (venv absent sous
# app/.venv). Peut aussi etre relance a la main (idempotent) pour reparer une install
# cassee. $PSScriptRoot = ABELr.lrplugin/ (le plugin embarque tout : app/,
# launch.ps1, bootstrap.ps1 -- copier ce seul dossier suffit sur une autre machine).
#
# Detection GPU : torch/torchvision sont installes CUDA (cu124) si un GPU NVIDIA est
# detecte (nvidia-smi present), sinon en build CPU (PyPI). Politique "GPU prioritaire,
# fallback CPU" -- cf. app/core/gpu.py.

$ErrorActionPreference = 'Stop'
$root    = $PSScriptRoot
$appDir  = Join-Path $root 'app'
$venvDir = Join-Path $appDir '.venv'
$venvPy  = Join-Path $venvDir 'Scripts\python.exe'
$reqFile = Join-Path $appDir 'requirements.txt'

Write-Host '[ABELr] Bootstrap -- verification de Python...'

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Error "Python introuvable dans le PATH. Installez Python 3.11+ (https://www.python.org/downloads/), cochez `"Add python.exe to PATH`", puis relancez."
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
        Write-Error "Python $maj.$min detecte -- 3.11+ requis pour ABELr."
        exit 1
    }
    Write-Host "[ABELr] Python $maj.$min OK."
} else {
    Write-Warning "Version Python non reconnue (`"$verOutput`") -- poursuite quand meme."
}

if (-not (Test-Path $venvPy)) {
    Write-Host "[ABELr] Creation du venv : $venvDir"
    python -m venv $venvDir
    if (-not (Test-Path $venvPy)) {
        Write-Error "Echec de creation du venv (python.exe absent apres `"python -m venv`")."
        exit 1
    }
} else {
    Write-Host '[ABELr] venv deja present -- reutilise.'
}

Write-Host '[ABELr] Mise a jour de pip...'
& $venvPy -m pip install --upgrade pip

# Detection GPU NVIDIA (nvidia-smi present = driver installe et fonctionnel).
$hasNvidia = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)

if ($hasNvidia) {
    Write-Host '[ABELr] GPU NVIDIA detecte -- installation torch CUDA (cu124)...'
    & $venvPy -m pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
} else {
    Write-Host '[ABELr] Aucun GPU NVIDIA detecte -- installation torch CPU (fallback).'
    & $venvPy -m pip install torch==2.6.0 torchvision==0.21.0
}
if ($LASTEXITCODE -ne 0) {
    Write-Error "Echec de l'installation de torch/torchvision (code $LASTEXITCODE)."
    exit 1
}

Write-Host '[ABELr] Installation des autres dependances (requirements.txt)...'
& $venvPy -m pip install -r $reqFile
if ($LASTEXITCODE -ne 0) {
    Write-Error "Echec de l'installation de requirements.txt (code $LASTEXITCODE)."
    exit 1
}

# exiftool : binaire externe non-pip (profil createur Sony CreativeStyle). Cherche
# d'abord un bundle local (bin/exiftool.exe), sinon le PATH systeme ; absence geree
# comme non bloquante par app/core/exif_profile.py (juste un profil manquant).
$exiftoolBundled = Join-Path $root 'bin\exiftool.exe'
$exiftoolInPath  = Get-Command exiftool -ErrorAction SilentlyContinue
if (-not $exiftoolInPath -and -not (Test-Path $exiftoolBundled)) {
    Write-Warning "exiftool introuvable (PATH ni bin\exiftool.exe) -- le profil createur Sony sera absent (non bloquant). Installer depuis https://exiftool.org si besoin."
}

Write-Host '[ABELr] Bootstrap termine.'
