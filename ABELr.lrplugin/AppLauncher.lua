--[[
    AppLauncher.lua — démarre / relance l'App Python depuis le plugin.

    Démarrage : lance `launch.ps1` (process détaché), dossier courant = le plugin
    (auto-suffisant, embarque `app/`), puis attend que /health réponde. Au tout
    premier lancement (pas de `.venv` sous le plugin), `launch.ps1` chaîne d'abord
    `bootstrap.ps1` (crée le venv + installe les dépendances, ~250 Mo à 2,5 Go selon
    GPU/CPU) — le timeout d'attente est donc bien plus long dans ce cas.
    Relance : POST /shutdown à l'App existante, attend son extinction, puis redémarre.

    Toutes les fonctions supposent tourner dans une tâche async (sleep + HTTP).
]]

local LrTasks     = import 'LrTasks'
local LrPathUtils = import 'LrPathUtils'
local LrFileUtils = import 'LrFileUtils'

local HttpClient = require 'HttpClient'
local Utils      = require 'Utils'

local AppLauncher = {}

-- Timeouts d'attente de /health (secondes) : lancement normal vs 1er run (bootstrap
-- télécharge les dépendances — torch CUDA/CPU ~250 Mo-2,5 Go, peut prendre plusieurs
-- minutes selon la connexion).
local HEALTH_TIMEOUT_NORMAL     = 12
local HEALTH_TIMEOUT_FIRST_RUN  = 900

-- True si le venv n'a pas encore été construit sous le plugin (1er lancement).
local function isFirstRun()
    local venvPython = LrPathUtils.child(
        LrPathUtils.child(LrPathUtils.child(Utils.appDir(), '.venv'), 'Scripts'),
        'python.exe')
    return not LrFileUtils.exists(venvPython)
end

-- Construit la commande PowerShell de lancement (Windows).
-- Lance launch.ps1 (dans le plugin) dans une fenêtre PowerShell détachée.
local function buildLaunchCommand()
    local script = LrPathUtils.child(Utils.projectRoot(), 'launch.ps1')
    return string.format(
        'cmd /c start "ABELr" powershell -ExecutionPolicy Bypass -File "%s"',
        script)
end

-- Attend que /health réponde (ou échoue) selon `wantAlive`. Retourne true si atteint.
local function waitForHealth(wantAlive, maxSeconds)
    local elapsed = 0
    local step = 0.4
    while elapsed < maxSeconds do
        if HttpClient.isAlive() == wantAlive then
            return true
        end
        LrTasks.sleep(step)
        elapsed = elapsed + step
    end
    return HttpClient.isAlive() == wantAlive
end

-- Lance le process App. Retourne (ok, message).
function AppLauncher.start()
    if HttpClient.isAlive() then
        return true, 'Application déjà démarrée.'
    end
    local firstRun = isFirstRun()
    local cmd = buildLaunchCommand()
    Utils.logf('Lancement App : %s (1er run = %s)', cmd, tostring(firstRun))
    LrTasks.execute(cmd)   -- détaché, rend la main aussitôt
    local timeout = firstRun and HEALTH_TIMEOUT_FIRST_RUN or HEALTH_TIMEOUT_NORMAL
    if waitForHealth(true, timeout) then
        return true, 'Application démarrée et connectée.'
    end
    if firstRun then
        return false,
            "Installation initiale en cours (venv + dépendances) — voir la fenêtre " ..
            "PowerShell ouverte. Relancez « Démarrer » une fois l'installation terminée " ..
            "si /health ne répond toujours pas."
    end
    return false, 'Lancement effectué mais /health ne répond pas (voir console Python).'
end

-- Demande l'arrêt de l'App via /shutdown. Retourne true si l'App s'est éteinte.
function AppLauncher.stop()
    if not HttpClient.isAlive() then
        return true
    end
    HttpClient.get('/health', 1)  -- réveille la socket si besoin
    -- /shutdown est un POST ; on le fait via postJson (corps vide).
    LrTasks.pcall(function() HttpClient.postJson('/shutdown', {}, 3) end)
    return waitForHealth(false, 6)
end

-- Relance : arrête l'instance existante puis démarre une instance neuve.
function AppLauncher.relaunch()
    AppLauncher.stop()
    LrTasks.sleep(0.5)
    return AppLauncher.start()
end

return AppLauncher
