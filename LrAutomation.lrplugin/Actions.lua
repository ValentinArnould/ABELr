--[[
    Actions.lua — actions haut niveau déclenchées par menu / boutons UI.

    Chaque action tourne dans postAsyncTaskWithContext (requis : HTTP GET + POST,
    sleep). Affiche un retour utilisateur via LrDialogs.
]]

local LrFunctionContext = import 'LrFunctionContext'
local LrDialogs         = import 'LrDialogs'

local AppLauncher = require 'AppLauncher'
local PollingLoop = require 'PollingLoop'
local HttpClient  = require 'HttpClient'

local Actions = {}

local function runAsync(name, fn)
    LrFunctionContext.postAsyncTaskWithContext(name, fn)
end

-- Démarre l'App (si besoin), puis le pont. Connexion complète.
function Actions.connect()
    runAsync('LrAutomationConnect', function()
        local ok, msg = AppLauncher.start()
        if ok then
            PollingLoop.start()
            msg = msg .. '\nPont actif (polling 300ms).'
        end
        LrDialogs.message('Lr Automation', msg, ok and 'info' or 'warning')
    end)
end

-- Relance l'App (arrêt propre + redémarrage), réactive le pont.
function Actions.relaunch()
    runAsync('LrAutomationRelaunch', function()
        local ok, msg = AppLauncher.relaunch()
        if ok then
            PollingLoop.start()
            msg = msg .. '\nPont actif (polling 300ms).'
        end
        LrDialogs.message('Lr Automation', msg, ok and 'info' or 'warning')
    end)
end

-- Vérifie l'état App + pont sans rien lancer.
function Actions.checkStatus()
    runAsync('LrAutomationStatus', function()
        local alive  = HttpClient.isAlive()
        local bridge = PollingLoop.isRunning()
        local msg = string.format(
            'Application : %s\nPont : %s',
            alive and 'connectée' or 'non démarrée',
            bridge and 'actif' or 'arrêté')
        LrDialogs.message('Lr Automation — statut', msg, 'info')
    end)
end

return Actions
