--[[
    Info.lua — ABELr plugin manifest
    Loaded by Lightroom Classic to identify and register the plugin.
]]

return {
    LrSdkVersion         = 12.0,
    LrSdkMinimumVersion  = 12.0,

    LrToolkitIdentifier  = 'com.abelr.plugin',
    LrPluginName         = 'ABELr',

    LrPluginInfoUrl      = '',

    -- Custom section in the Plug-in Manager
    LrPluginInfoProvider = 'PluginInfoProvider.lua',

    -- Library > Plug-in Extras (active Library module)
    LrLibraryMenuItems   = {
        {
            title = 'Start / connect the application',
            file  = 'MenuConnect.lua',
        },
        {
            title = 'Relaunch the application',
            file  = 'MenuRelaunch.lua',
        },
        {
            title = 'test',
            file  = 'ShowMessage.lua',
        }
    },

    -- File > Plug-in Extras
    LrExportMenuItems    = {
        {
            title = 'Start / connect the application',
            file  = 'MenuConnect.lua',
        },
        {
            title = 'Relaunch the application',
            file  = 'MenuRelaunch.lua',
        },
        {
            title = 'test',
            file  = 'ShowMessage.lua',
        }
    },

    -- Help > Plug-in Extras
    LrHelpMenuItems      = {
        {
            title = 'Start / connect the application',
            file  = 'MenuConnect.lua',
        },
        {
            title = 'Relaunch the application',
            file  = 'MenuRelaunch.lua',
        },
        {
            title = 'test',
            file  = 'ShowMessage.lua',
        }
    },

    VERSION              = { major = 0, minor = 1, revision = 0, build = 1 },
}
