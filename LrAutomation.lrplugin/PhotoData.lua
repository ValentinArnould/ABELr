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
-- historique côté App (les seeds sont marqués en DB via cache.is_seed).
--
-- CameraProfile + ProcessVersion : clé du modèle de réponse calibré côté App
-- (la réponse ∂rendu/∂curseur dépend du profil DCP). Les 24 curseurs HSL servent à
-- connaître l'état couleur courant (core.hsl). Tons (Contrast/Highlights/…) déjà là.
local DEVELOP_KEYS = {
    'WhiteBalance', 'Temperature', 'Tint',
    'Exposure2012', 'Contrast2012', 'Highlights2012', 'Shadows2012',
    'Whites2012', 'Blacks2012', 'Clarity2012', 'Dehaze',
    'Vibrance', 'Saturation',
    'CameraProfile', 'ProcessVersion',
    -- Recadrage : entre dans la clé de style du rendu neutre côté App (un crop
    -- change le rendu des miniatures → l'ancre doit être recalculée).
    'CropLeft', 'CropRight', 'CropTop', 'CropBottom', 'CropAngle',
    -- HSL — 8 bandes × {Hue, Saturation, Luminance} (noms SDK).
    'HueAdjustmentRed', 'HueAdjustmentOrange', 'HueAdjustmentYellow',
    'HueAdjustmentGreen', 'HueAdjustmentAqua', 'HueAdjustmentBlue',
    'HueAdjustmentPurple', 'HueAdjustmentMagenta',
    'SaturationAdjustmentRed', 'SaturationAdjustmentOrange', 'SaturationAdjustmentYellow',
    'SaturationAdjustmentGreen', 'SaturationAdjustmentAqua', 'SaturationAdjustmentBlue',
    'SaturationAdjustmentPurple', 'SaturationAdjustmentMagenta',
    'LuminanceAdjustmentRed', 'LuminanceAdjustmentOrange', 'LuminanceAdjustmentYellow',
    'LuminanceAdjustmentGreen', 'LuminanceAdjustmentAqua', 'LuminanceAdjustmentBlue',
    'LuminanceAdjustmentPurple', 'LuminanceAdjustmentMagenta',
    -- Style non neutralisé par le probe : entre dans hash_style côté App
    -- (revue Fable 5 DB-01). Noms hybrides Color Grading : ombres/HL Hue+Sat =
    -- SplitToning*, le reste ColorGrade* (cf. lr15_sdk_api_reference §Color Grading).
    'Texture',
    'SplitToningShadowHue', 'SplitToningShadowSaturation',
    'SplitToningHighlightHue', 'SplitToningHighlightSaturation',
    'SplitToningBalance',
    'ColorGradeShadowLum', 'ColorGradeHighlightLum',
    'ColorGradeMidtoneHue', 'ColorGradeMidtoneSat', 'ColorGradeMidtoneLum',
    'ColorGradeGlobalHue', 'ColorGradeGlobalSat', 'ColorGradeGlobalLum',
    'ColorGradeBlending',
    'ParametricShadows', 'ParametricDarks', 'ParametricLights', 'ParametricHighlights',
    'ParametricShadowSplit', 'ParametricMidtoneSplit', 'ParametricHighlightSplit',
    -- Courbe par points : tables de nombres (sérialisées telles quelles en JSON).
    'ToneCurveName2012', 'ToneCurvePV2012',
    'ToneCurvePV2012Red', 'ToneCurvePV2012Green', 'ToneCurvePV2012Blue',
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

-- Table sérialisable JSON pour une photo (clés snake_case, develop PascalCase SDK).
local function photoToTable(photo, catalogPath)
    return {
        photo_id        = photo:getRawMetadata('uuid'),
        path            = photo:getRawMetadata('path'),
        catalog_path    = catalogPath,
        exif            = extractExif(photo),
        current_develop = extractDevelop(photo),
    }
end

local function photosToArray(photos, catalogPath)
    local result = Json.array({})
    for _, photo in ipairs(photos) do
        result[#result + 1] = photoToTable(photo, catalogPath)
    end
    return result
end

-- Retourne un tableau JSON (Json.array) des photos sélectionnées (cible active).
function PhotoData.getSelectedPhotos()
    local catalog     = LrApplication.activeCatalog()
    local catalogPath = catalog:getPath()  -- chemin du .lrcat → localise les .lrdata
    return photosToArray(catalog:getTargetPhotos(), catalogPath)
end

-- Retourne un tableau JSON de TOUTES les photos du catalogue actif (index App).
function PhotoData.getAllPhotos()
    local catalog     = LrApplication.activeCatalog()
    local catalogPath = catalog:getPath()
    return photosToArray(catalog:getAllPhotos(), catalogPath)
end

return PhotoData
