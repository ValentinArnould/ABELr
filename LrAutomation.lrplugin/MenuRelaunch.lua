--[[
    MenuRelaunch.lua — Bibliothèque > Modules externes > Relancer l'application.
    Arrêt propre de l'instance existante puis redémarrage. Vide le cache des
    modules du plugin pour appliquer le nouveau code après un rechargement.
]]

if package and package.loaded then
    for _, m in ipairs({ 'Actions', 'PollingLoop', 'PhotoData', 'Adjustments',
                         'Thumbnails', 'HttpClient', 'AppLauncher', 'Utils', 'Json' }) do
        package.loaded[m] = nil
    end
end

local Actions = require 'Actions'
Actions.relaunch()
