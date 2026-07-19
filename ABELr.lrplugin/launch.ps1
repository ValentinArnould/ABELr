# launch.ps1 -- lance par le plugin Lr pour demarrer l'App Python.
# $PSScriptRoot = ABELr.lrplugin/ (le plugin est auto-suffisant : embarque
# app/, ce script et bootstrap.ps1 -- copier ce seul dossier suffit sur une autre
# machine, cf. Utils.lua / AppLauncher.lua).

$root   = $PSScriptRoot
Set-Location $root

$venvPy = Join-Path $root 'app\.venv\Scripts\python.exe'

if (-not (Test-Path $venvPy)) {
    Write-Host '[ABELr] Premiere installation detectee -- lancement du bootstrap (venv + dependances)...'
    & (Join-Path $root 'bootstrap.ps1')
    if (-not (Test-Path $venvPy)) {
        Write-Host ''
        Write-Host '[ABELr] ERREUR : bootstrap termine mais python.exe introuvable dans le venv.' -ForegroundColor Red
        Write-Host 'Voir les messages ci-dessus pour la cause (Python absent, echec pip, etc.).'
        Read-Host 'Appuyez sur Entree pour fermer cette fenetre'
        exit 1
    }
}

# exiftool bundle (bin/) prioritaire dans le PATH si present -- autonomie complete
# sur une machine cible sans exiftool installe globalement.
$binDir = Join-Path $root 'bin'
if (Test-Path $binDir) {
    $env:PATH = "$binDir;$env:PATH"
}

& $venvPy -m app.main
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host ''
    Write-Host "[ABELr] L'application s'est arretee avec le code $exitCode." -ForegroundColor Red
    Read-Host 'Appuyez sur Entree pour fermer cette fenetre'
}
exit $exitCode
