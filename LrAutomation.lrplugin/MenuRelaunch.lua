--[[
    MenuRelaunch.lua — Bibliothèque > Modules externes > Relancer l'application.
    Arrêt propre de l'instance existante puis redémarrage. Vide le cache des
    modules du plugin pour appliquer le nouveau code après un rechargement.
]]

for _, m in ipairs({ 'Actions', 'PollingLoop', 'PhotoData', 'Adjustments',
                     'Thumbnails', 'HttpClient', 'AppLauncher', 'Utils', 'Json' }) do
    package.loaded[m] = nil
end

local Actions = require 'Actions'
Actions.relaunch()
