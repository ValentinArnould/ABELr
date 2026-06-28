--[[
    Adjustments.lua — application des ajustements develop via SDK.

    Toute écriture passe par catalog:withWriteAccessDo. Les photos sont retrouvées
    par uuid parmi la sélection courante (mapping simple v1).
]]

local LrApplication = import 'LrApplication'

local Adjustments = {}

-- adjustments : liste de { photo_id = uuid, develop = { PascalCase = valeur } }.
-- Retourne (applied, total).
function Adjustments.apply(adjustments)
    local catalog = LrApplication.activeCatalog()

    -- Index uuid → photo sur la sélection courante.
    local byUuid = {}
    for _, photo in ipairs(catalog:getTargetPhotos()) do
        byUuid[photo:getRawMetadata('uuid')] = photo
    end

    local applied = 0
    local total   = #adjustments

    catalog:withWriteAccessDo('Lr Automation : ajustements', function()
        for _, adj in ipairs(adjustments) do
            local photo = byUuid[adj.photo_id]
            if photo and adj.develop then
                photo:applyDevelopSettings(adj.develop)
                applied = applied + 1
            end
        end
    end)

    return applied, total
end

return Adjustments
