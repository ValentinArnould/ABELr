--[[
    MenuConnect.lua — Library > Plug-in Extras > Start the application.
    Launches the Python App (if needed) then activates the polling bridge.

    Clears the plugin module cache before loading them: guarantees that
    reloading the plugin (Plugin Manager > Reload) applies the new code
    immediately on the next click of this menu (without having to restart Lr).
]]

-- Clears the plugin modules to force a reload from disk.
-- The Lr SDK modules (import '...') are not in package.loaded, not affected.
if package and package.loaded then
    for _, m in ipairs({ 'Actions', 'PollingLoop', 'PhotoData', 'Adjustments',
                         'Thumbnails', 'HttpClient', 'AppLauncher', 'Utils', 'Json' }) do
        package.loaded[m] = nil
    end
end

local Actions = require 'Actions'
Actions.connect()
