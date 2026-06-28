# launch_app.ps1 — lancé par le plugin Lr pour démarrer l'App Python.
# $PSScriptRoot = racine du projet (Lr_automation/).

$root = $PSScriptRoot
Set-Location $root
$app = Join-Path $root "app"
$venvPy = Join-Path $app ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPy) { $venvPy } else { "python" }

& $python -m app.main
