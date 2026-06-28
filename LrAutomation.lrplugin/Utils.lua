--[[
    Utils.lua — helpers partagés : logger, chemins projet.
]]

local LrPathUtils = import 'LrPathUtils'
local LrLogger    = import 'LrLogger'

local Utils = {}

-- Logger : visible via fichier de log + Console Lua.
local log = LrLogger('LrAutomation')
log:enable('print')   -- 'print' → Console Lua ; passer à 'logfile' pour fichier
Utils.log = log

function Utils.logf(fmt, ...)
    log:trace(string.format(fmt, ...))
end

-- Racine du projet = deux niveaux au-dessus du dossier .lrplugin.
--   _PLUGIN.path = .../Lr_automation/plugin/LrAutomation.lrplugin
--   parent       = .../Lr_automation/plugin
--   parent       = .../Lr_automation
function Utils.projectRoot()
    return LrPathUtils.parent(LrPathUtils.parent(_PLUGIN.path))
end

-- Dossier de l'app Python (.../Lr_automation/app).
function Utils.appDir()
    return LrPathUtils.child(Utils.projectRoot(), 'app')
end

return Utils
