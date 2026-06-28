--[[
    AppLauncher.lua — démarre / relance l'App Python depuis le plugin.

    Démarrage : lance `python -m app.main` (process détaché) avec la racine projet
    comme dossier courant, puis attend que /health réponde.
    Relance : POST /shutdown à l'App existante, attend son extinction, puis redémarre.

    Toutes les fonctions supposent tourner dans une tâche async (sleep + HTTP).
]]

local LrTasks     = import 'LrTasks'
local LrPathUtils = import 'LrPathUtils'

local HttpClient = require 'HttpClient'
local Utils      = require 'Utils'

local AppLauncher = {}

-- Construit la commande PowerShell de lancement (Windows).
-- Lance launch_app.ps1 dans une fenêtre PowerShell détachée.
local function buildLaunchCommand()
    -- _PLUGIN.path = .../Lr_automation/LrAutomation.lrplugin → parent = racine projet
    local projectRoot = LrPathUtils.parent(_PLUGIN.path)
    local script      = LrPathUtils.child(projectRoot, 'launch_app.ps1')
    return string.format(
        'cmd /c start "Lr Automation" powershell -ExecutionPolicy Bypass -File "%s"',
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
