--[[
    PluginInfoProvider.lua
    Section custom affichée dans Fichier > Gestionnaire des modules externes,
    quand "Lr Automation" est sélectionné dans la liste de gauche.

    Boutons : démarrer/connecter, relancer l'App Python, vérifier l'état.
    Référencé par la clé LrPluginInfoProvider de Info.lua.
]]

local LrView  = import 'LrView'
local LrColor = import 'LrColor'

local Actions = require 'Actions'
local Utils = require 'Utils'

local provider = {}

function provider.sectionsForTopOfDialog(f, propertyTable)
    return {
        {
            title = 'Lr Automation',

            f:row {
                f:static_text {
                    title = 'Retouche batch intelligente pilotée par application externe.',
                    fill_horizontal = 1,
                },
            },

            f:row {
                f:push_button {
                    title  = 'Démarrer / connecter',
                    action = function() Actions.connect() end,
                },
                f:push_button {
                    title  = 'Relancer l\'application',
                    action = function() Actions.relaunch() end,
                },
                f:push_button {
                    title  = 'Vérifier l\'état',
                    action = function() Actions.checkStatus() end,
                },
                f:push_button {
                    title  = 'test',
                    action = function() Utils.test() end,
                },
            },

            f:row {
                f:static_text {
                    title      = 'Serveur attendu : http://127.0.0.1:5000',
                    text_color = LrColor(0.5, 0.5, 0.5),
                },
            },
        },
    }
end

return provider
