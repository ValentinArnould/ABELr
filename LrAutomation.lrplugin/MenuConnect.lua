--[[
    MenuConnect.lua — Bibliothèque > Modules externes > Démarrer l'application.
    Lance l'App Python (si besoin) puis active le pont de polling.

    Vide le cache des modules du plugin avant de les charger : garantit que le
    rechargement du plugin (Plugin Manager > Recharger) applique le nouveau code
    immédiatement lors du prochain clic sur ce menu (sans avoir à relancer Lr).
]]

-- Vide les modules du plugin pour forcer rechargement depuis le disque.
-- Les modules SDK Lr (import '...') ne sont pas dans package.loaded, pas touchés.
for _, m in ipairs({ 'Actions', 'PollingLoop', 'PhotoData', 'Adjustments',
                     'Thumbnails', 'HttpClient', 'AppLauncher', 'Utils', 'Json' }) do
    package.loaded[m] = nil
end

local Actions = require 'Actions'
Actions.connect()
