--[[
    AppLauncher.lua — démarre / relance l'App Python depuis le plugin.

    Démarrage : lance `python -m app.main` (process détaché) avec la racine projet
    comme dossier courant, puis attend que /health réponde.
    Relance : POST /shutdown à l'App existante, attend son extinction, puis redémarre.

    Toutes les fonctions supposent tourner dans une tâche async (sleep + HTTP).
]]

local LrTasks     = import 'LrTasks'
local LrPathUtils = import 'LrPathUtils'
local LrFileUtils = import 'LrFileUtils'

local HttpClient = require 'HttpClient'
local Utils      = require 'Utils'

local AppLauncher = {}

-- Chemin de l'interpréteur Python : venv du projet en priorité, sinon PATH.
local function pythonExe()
    local venvPy = LrPathUtils.child(
        LrPathUtils.child(
            LrPathUtils.child(Utils.appDir(), '.venv'), 'Scripts'), 'python.exe')
    if LrFileUtils.exists(venvPy) then
        return venvPy
    end
    return 'python'   -- depuis le PATH système
end

-- Construit la commande de lancement détaché (Windows).
-- `start "titre" /D <cwd> <exe> -m app.main` rend la main immédiatement.
local function buildLaunchCommand()
    local root = Utils.projectRoot()
    local exe  = pythonExe()
    -- Le premier argument quoté de `start` est interprété comme titre → on le fournit
    -- explicitement pour lever l'ambiguïté quand <exe> est quoté.
    return string.format(
        'cmd /c start "Lr Automation" /D "%s" "%s" -m app.main',
        root, exe)
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
    local cmd = buildLaunchCommand()
    Utils.logf('Lancement App : %s', cmd)
    LrTasks.execute(cmd)   -- détaché, rend la main aussitôt
    if waitForHealth(true, 12) then
        return true, 'Application démarrée et connectée.'
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
    pcall(function() HttpClient.postJson('/shutdown', {}, 3) end)
    return waitForHealth(false, 6)
end

-- Relance : arrête l'instance existante puis démarre une instance neuve.
function AppLauncher.relaunch()
    AppLauncher.stop()
    LrTasks.sleep(0.5)
    return AppLauncher.start()
end

return AppLauncher
