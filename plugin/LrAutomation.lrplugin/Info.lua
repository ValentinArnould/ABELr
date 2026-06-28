return {
    LrSdkVersion        = 12.0,
    LrSdkMinimumVersion = 12.0,

    LrToolkitIdentifier = 'com.lrautomation.plugin',
    LrPluginName        = 'Lr Automation',

    -- Dossier où Lightroom peut stocker les fichiers temporaires du plugin
    LrPluginInfoUrl = "",

    -- LrInitPlugin = "Init.lua",

    -- LrPluginInfoProvider = "lua/PluginInfoProvider.lua",

    LrLibraryMenuItems = {
        {
            title       = 'Lr Automation',
            file        = 'Menu.lua',
            enabledWhen = 'anythingSelected',
        },
    },

    VERSION = { major = 0, minor = 1, revision = 0, build = 1 },
}
