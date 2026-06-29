--[[
    Utils.lua — helpers partagés : logger, chemins projet.
]]

local LrPathUtils = import 'LrPathUtils'
local LrLogger    = import 'LrLogger'
local LrDialogs   = import 'LrDialogs'

local Utils = {}

-- Logger : visible via fichier de log + Console Lua.
local log = LrLogger('LrAutomation')
log:enable('print')   -- 'print' → Console Lua ; passer à 'logfile' pour fichier
Utils.log = log

function Utils.logf(fmt, ...)
    log:trace(string.format(fmt, ...))
end

-- Racine du projet = dossier parent du .lrplugin (le plugin est chargé
-- directement depuis la racine du projet).
--   _PLUGIN.path = .../Lr_automation/LrAutomation.lrplugin
--   parent       = .../Lr_automation
-- (doit rester cohérent avec AppLauncher.buildLaunchCommand, qui calcule
--  la racine de la même façon.)
function Utils.projectRoot()
    return LrPathUtils.parent(_PLUGIN.path)
end

-- Dossier de l'app Python (.../Lr_automation/app).
function Utils.appDir()
    return LrPathUtils.child(Utils.projectRoot(), 'app')
end

function Utils.test()
    LrDialogs.message('Lr Automation', 'Hello World', 'info')
end

return Utils
