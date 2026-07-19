# launch.ps1 -- launched by the Lr plugin to start the Python App.
# $PSScriptRoot = ABELr.lrplugin/ (the plugin is self-sufficient: embeds
# app/, this script and bootstrap.ps1 -- copying just this folder is enough on another
# machine, see Utils.lua / AppLauncher.lua).

$root   = $PSScriptRoot
Set-Location $root

$venvPy = Join-Path $root 'app\.venv\Scripts\python.exe'

if (-not (Test-Path $venvPy)) {
    Write-Host '[ABELr] First-time install detected -- launching bootstrap (venv + dependencies)...'
    & (Join-Path $root 'bootstrap.ps1')
    if (-not (Test-Path $venvPy)) {
        Write-Host ''
        Write-Host '[ABELr] ERROR: bootstrap finished but python.exe not found in the venv.' -ForegroundColor Red
        Write-Host 'See the messages above for the cause (Python missing, pip failure, etc.).'
        Read-Host 'Press Enter to close this window'
        exit 1
    }
}

# Bundled exiftool (bin/) takes priority in the PATH if present -- fully self-contained
# on a target machine without a globally installed exiftool.
$binDir = Join-Path $root 'bin'
if (Test-Path $binDir) {
    $env:PATH = "$binDir;$env:PATH"
}

& $venvPy -m app.main
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host ''
    Write-Host "[ABELr] The application stopped with code $exitCode." -ForegroundColor Red
    Read-Host 'Press Enter to close this window'
}
exit $exitCode
