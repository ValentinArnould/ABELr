--[[
    PhotoData.lua — extraction des données photo via SDK (path, EXIF, develop).

    Construit les tables sérialisables JSON attendues par l'App (clés snake_case ;
    develop settings en PascalCase SDK).
]]

local LrApplication = import 'LrApplication'
local Json          = require 'Json'

local PhotoData = {}

-- Sous-ensemble de develop settings utile à l'analyse batch.
-- Noms SDK = PV2012 (Exposure2012, etc.) : ce sont les valeurs réellement réglées
-- par l'utilisateur. WhiteBalance ("Custom" = WB posée à la main) sert de marqueur
-- de seed côté App (core.seeds.is_seed).
local DEVELOP_KEYS = {
    'WhiteBalance', 'Temperature', 'Tint',
    'Exposure2012', 'Contrast2012', 'Highlights2012', 'Shadows2012',
    'Whites2012', 'Blacks2012', 'Clarity2012', 'Dehaze',
    'Vibrance', 'Saturation',
}

local function extractExif(photo)
    return {
        iso           = photo:getRawMetadata('isoSpeedRating'),
        aperture      = photo:getRawMetadata('aperture'),
        shutter_speed = photo:getFormattedMetadata('shutterSpeed'),
        focal_length  = photo:getRawMetadata('focalLength'),
        camera        = photo:getFormattedMetadata('cameraModel'),
    }
end

local function extractDevelop(photo)
    local settings = photo:getDevelopSettings()
    local out = {}
    for _, key in ipairs(DEVELOP_KEYS) do
        local v = settings[key]
        if v ~= nil then out[key] = v end
    end
    return out
end

-- Retourne un tableau JSON (Json.array) de photos pour les photos sélectionnées.
function PhotoData.getSelectedPhotos()
    local catalog     = LrApplication.activeCatalog()
    local catalogPath = catalog:getPath()  -- chemin du .lrcat → localise les .lrdata
    local photos      = catalog:getTargetPhotos()
    local result      = Json.array({})
    for _, photo in ipairs(photos) do
        result[#result + 1] = {
            photo_id        = photo:getRawMetadata('uuid'),
            path            = photo:getRawMetadata('path'),
            catalog_path    = catalogPath,
            exif            = extractExif(photo),
            current_develop = extractDevelop(photo),
        }
    end
    return result
end

return PhotoData
