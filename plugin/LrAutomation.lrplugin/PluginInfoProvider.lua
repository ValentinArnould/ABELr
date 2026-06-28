--[[
    PluginInfoProvider.lua
    Section custom affichée dans Fichier > Gestionnaire des modules externes,
    quand "Lr Automation" est sélectionné dans la liste de gauche.

    Référencé par la clé LrPluginInfoProvider de Info.lua.
    API : retourne une table avec sectionsForTopOfDialog / sectionsForBottomOfDialog,
    chacune = fonction(viewFactory, propertyTable) -> liste de sections de vue.
]]

local LrView    = import 'LrView'
local LrDialogs = import 'LrDialogs'
local LrTasks   = import 'LrTasks'

local provider = {}

function provider.sectionsForTopOfDialog(f, propertyTable)
    return {
        {
            title = 'Lr Automation',

            f:row {
                f:static_text {
                    title = 'Retouche batch intelligente pilotée par application externe.',
                    fill_horizontal = 1,
                },
            },

            f:row {
                f:static_text { title = 'Statut application :', font = '<system/bold>' },
                f:static_text { title = 'non connectée' },
            },

            f:row {
                f:push_button {
                    title  = 'Test Hello World',
                    action = function()
                        LrTasks.startAsyncTask(function()
                            LrDialogs.message('Lr Automation', 'Hello World', 'info')
                        end)
                    end,
                },
            },
        },
    }
end

return provider
