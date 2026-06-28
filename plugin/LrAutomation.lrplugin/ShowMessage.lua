--[[
    ShowMessage.lua — Fonction de test : affiche une popup "Hello World".
    Déclenché depuis Bibliothèque > Modules externes > Hello World.
]]

local LrDialogs = import 'LrDialogs'
local LrTasks   = import 'LrTasks'

local function showMessage()
    LrDialogs.message('Lr Automation', 'Hello World', 'info')
end

-- Tout appel SDK pouvant bloquer doit tourner dans une tâche asynchrone.
LrTasks.startAsyncTask(showMessage)
