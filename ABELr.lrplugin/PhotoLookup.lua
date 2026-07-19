--[[
    PhotoLookup.lua — résout une liste d'uuids → photos LrPhoto.

    Même logique que Adjustments.apply / Thumbnails.fetchProbe : on indexe la
    sélection courante (getTargetPhotos) puis on replie sur findPhotoByUuid (la
    sélection a pu changer entre la lecture côté App et l'action). Factorisé ici
    pour les handlers Phase 2 (Metadata / Collections / Presets).
]]

local LrApplication = import 'LrApplication'

local PhotoLookup = {}

-- Retourne (matched, missing).
--   matched = { { id = uuid, photo = LrPhoto }, ... }  (ordre d'entrée préservé)
--   missing = { uuid, ... }
function PhotoLookup.resolve(photoIds)
    local catalog = LrApplication.activeCatalog()
    local byUuid = {}
    for _, photo in ipairs(catalog:getTargetPhotos()) do
        byUuid[photo:getRawMetadata('uuid')] = photo
    end
    local matched, missing = {}, {}
    for _, id in ipairs(photoIds or {}) do
        local p = byUuid[id] or catalog:findPhotoByUuid(id)
        if p then
            matched[#matched + 1] = { id = id, photo = p }
        else
            missing[#missing + 1] = id
        end
    end
    return matched, missing
end

return PhotoLookup
