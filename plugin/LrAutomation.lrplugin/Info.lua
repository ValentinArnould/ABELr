--[[
    Info.lua — Manifeste du plugin Lr Automation
    Chargé par Lightroom Classic pour identifier et enregistrer le plugin.
]]

return {
    LrSdkVersion        = 12.0,
    LrSdkMinimumVersion = 12.0,

    LrToolkitIdentifier = 'com.lrautomation.plugin',
    LrPluginName        = 'Lr Automation',

    LrPluginInfoUrl     = '',

    -- Section custom dans le Gestionnaire de modules externes
    LrPluginInfoProvider = 'PluginInfoProvider.lua',

    -- Bibliothèque > Modules externes supplémentaires (module Bibliothèque actif)
    LrLibraryMenuItems = {
        {
            title = 'Hello World',
            file  = 'ShowMessage.lua',
        },
    },

    -- Fichier > Modules externes supplémentaires
    LrExportMenuItems = {
        {
            title = 'Hello World',
            file  = 'ShowMessage.lua',
        },
    },

    VERSION = { major = 0, minor = 1, revision = 0, build = 1 },
}
