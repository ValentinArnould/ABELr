--[[
    PluginInfoProvider.lua
    Custom section shown in File > Plug-in Manager,
    when "ABELr" is selected in the left-hand list.

    Buttons: start/connect, relaunch the Python App, check status.
    Referenced by the LrPluginInfoProvider key in Info.lua.
]]

local LrView  = import 'LrView'
local LrColor = import 'LrColor'

local Actions = require 'Actions'
local Utils = require 'Utils'

local provider = {}

function provider.sectionsForTopOfDialog(f, propertyTable)
    return {
        {
            title = 'ABELr',

            f:row {
                f:static_text {
                    title = 'Intelligent batch retouching driven by an external application.',
                    fill_horizontal = 1,
                },
            },

            f:row {
                f:push_button {
                    title  = 'Start / connect',
                    action = function() Actions.connect() end,
                },
                f:push_button {
                    title  = 'Relaunch application',
                    action = function() Actions.relaunch() end,
                },
                f:push_button {
                    title  = 'Check status',
                    action = function() Actions.checkStatus() end,
                },
                f:push_button {
                    title  = 'test',
                    action = function() Utils.test() end,
                },
            },

            f:row {
                f:static_text {
                    title      = 'Expected server: http://127.0.0.1:5000',
                    text_color = LrColor(0.5, 0.5, 0.5),
                },
            },
        },
    }
end

return provider
