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

-- Racine du projet = le plugin lui-même : depuis la refonte "plugin auto-suffisant",
-- LrAutomation.lrplugin/ embarque tout (app/, launch.ps1, bootstrap.ps1) — plus besoin
-- d'un dossier parent avec le projet complet. Copier ce seul dossier .lrplugin suffit
-- à installer sur une autre machine.
--   _PLUGIN.path = .../LrAutomation.lrplugin
-- (doit rester cohérent avec AppLauncher.buildLaunchCommand, qui calcule
--  la racine de la même façon.)
function Utils.projectRoot()
    return _PLUGIN.path
end

-- Dossier de l'app Python (.../LrAutomation.lrplugin/app).
function Utils.appDir()
    return LrPathUtils.child(Utils.projectRoot(), 'app')
end

-- Répertoire des miniatures temporaires (.../LrAutomation.lrplugin/tmp_thumbs).
function Utils.thumbsDir()
    return LrPathUtils.child(Utils.projectRoot(), 'tmp_thumbs')
end

function Utils.test()
    LrDialogs.message('Lr Automation', 'Hello World', 'info')
end

-- Liste "clé=valeur" d'une table (diagnostic des develop settings reçus).
function Utils.dumpKeys(t)
    if type(t) ~= 'table' then return tostring(t) end
    local parts = {}
    for k, v in pairs(t) do
        parts[#parts + 1] = tostring(k) .. '=' .. tostring(v)
    end
    return table.concat(parts, ', ')
end

return Utils
