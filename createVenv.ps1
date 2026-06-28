
# Set-Location dans le dossier du script
Set-Location -Path $PSScriptRoot

# si pas de dir app/.env, on crée le virtualenv et on installe les dépendances
if (!(Test-Path -Path "app/.venv")) {
    Write-Host "Creating virtual environment..."
    python -m virtualenv app/.venv
}
Write-Host "Installing dependencies..."
Set-Location app
.\.venv\Scripts\activate
# mettre à jour les dépendances si besoin
pip install --upgrade -r requirements.txt