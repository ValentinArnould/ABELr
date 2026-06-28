local LrView    = import 'LrView'
local LrDialogs = import 'LrDialogs'

local Dialog    = {}

function Dialog.showMain()
    local f = LrView.osFactory()

    local contents = f:column {
        spacing = f:dialog_spacing(),

        f:static_text {
            title = 'Lr Automation — v0.1',
            font  = '<system/bold>',
        },

        f:separator { fill_horizontal = 1 },

        f:static_text {
            title = 'Retouche intelligente pilotée par application externe.',
        },

        f:spacer { height = 8 },

        f:row {
            f:static_text { title = 'Statut application :', font = '<system/bold>' },
            f:static_text { title = 'non connectée' },
        },
    }

    LrDialogs.presentModalDialog {
        title      = 'Lr Automation',
        contents   = contents,
        actionVerb = 'OK',
        cancelVerb = '< exclude >',
    }
end

return Dialog
