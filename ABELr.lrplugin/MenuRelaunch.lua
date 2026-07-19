--[[
    MenuRelaunch.lua — Library > Plug-in Extras > Relaunch the application.
    Cleanly stops the existing instance then restarts it. Clears the plugin's
    module cache so new code takes effect after a reload.
]]

if package and package.loaded then
    for _, m in ipairs({ 'Actions', 'PollingLoop', 'PhotoData', 'Adjustments',
                         'Thumbnails', 'HttpClient', 'AppLauncher', 'Utils', 'Json' }) do
        package.loaded[m] = nil
    end
end

local Actions = require 'Actions'
Actions.relaunch()
