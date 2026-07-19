--[[
    Info.lua — Manifeste du plugin ABELr
    Chargé par Lightroom Classic pour identifier et enregistrer le plugin.
]]

return {
    LrSdkVersion         = 12.0,
    LrSdkMinimumVersion  = 12.0,

    LrToolkitIdentifier  = 'com.abelr.plugin',
    LrPluginName         = 'ABELr',

    LrPluginInfoUrl      = '',

    -- Section custom dans le Gestionnaire de modules externes
    LrPluginInfoProvider = 'PluginInfoProvider.lua',

    -- Bibliothèque > Modules externes supplémentaires (module Bibliothèque actif)
    LrLibraryMenuItems   = {
        {
            title = 'Démarrer / connecter l\'application',
            file  = 'MenuConnect.lua',
        },
        {
            title = 'Relancer l\'application',
            file  = 'MenuRelaunch.lua',
        },
        {
            title = 'test',
            file  = 'ShowMessage.lua',
        }
    },

    -- Fichier > Modules externes supplémentaires
    LrExportMenuItems    = {
        {
            title = 'Démarrer / connecter l\'application',
            file  = 'MenuConnect.lua',
        },
        {
            title = 'Relancer l\'application',
            file  = 'MenuRelaunch.lua',
        },
        {
            title = 'test',
            file  = 'ShowMessage.lua',
        }
    },

    -- Aide > Modules externes supplémentaires
    LrHelpMenuItems      = {
        {
            title = 'Démarrer / connecter l\'application',
            file  = 'MenuConnect.lua',
        },
        {
            title = 'Relancer l\'application',
            file  = 'MenuRelaunch.lua',
        },
        {
            title = 'test',
            file  = 'ShowMessage.lua',
        }
    },

    VERSION              = { major = 0, minor = 1, revision = 0, build = 1 },
}
