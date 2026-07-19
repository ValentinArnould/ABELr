--[[
    PhotoLookup.lua — resolves a list of uuids → LrPhoto photos.

    Same logic as Adjustments.apply / Thumbnails.fetchProbe: index the
    current selection (getTargetPhotos) then fall back to findPhotoByUuid (the
    selection may have changed between the App-side read and the action). Factored out here
    for the Phase 2 handlers (Metadata / Collections / Presets).
]]

local LrApplication = import 'LrApplication'

local PhotoLookup = {}

-- Returns (matched, missing).
--   matched = { { id = uuid, photo = LrPhoto }, ... }  (input order preserved)
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
