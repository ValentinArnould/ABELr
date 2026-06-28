--[[
    ShowMessage.lua — Fonction de test : affiche une popup "Hello World".
    Déclenché depuis Bibliothèque > Modules externes > Hello World.
]]

local Utils = require 'Utils'
Utils.test()

-- Tout appel SDK pouvant bloquer doit tourner dans une tâche asynchrone.
-- LrTasks.startAsyncTask(showMessage)
