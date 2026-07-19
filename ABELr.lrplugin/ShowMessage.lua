--[[
    ShowMessage.lua — Test function: shows a "Hello World" popup.
    Triggered from Library > Plug-in Extras > Hello World.
]]

local Utils = require 'Utils'
Utils.test()

-- Any SDK call that could block must run inside an async task.
-- LrTasks.startAsyncTask(showMessage)
