--[[
    PhotoData.lua — extracts photo data via the SDK (path, EXIF, develop).

    Builds the JSON-serializable tables expected by the App (snake_case keys;
    develop settings in SDK PascalCase).
]]

local LrApplication = import 'LrApplication'
local Json          = require 'Json'

local PhotoData = {}

-- Subset of develop settings useful for batch analysis.
-- SDK names = PV2012 (Exposure2012, etc.): these are the values actually set
-- by the user. WhiteBalance ("Custom" = WB set by hand) serves as a
-- historical marker on the App side (seeds are marked in the DB via cache.is_seed).
--
-- CameraProfile + ProcessVersion: key to the response model calibrated on the App side
-- (the ∂render/∂slider response depends on the DCP profile). The 24 HSL sliders are
-- used to know the current color state (core.hsl). Tones (Contrast/Highlights/…) already covered.
local DEVELOP_KEYS = {
    'WhiteBalance', 'Temperature', 'Tint',
    'Exposure2012', 'Contrast2012', 'Highlights2012', 'Shadows2012',
    'Whites2012', 'Blacks2012', 'Clarity2012', 'Dehaze',
    'Vibrance', 'Saturation',
    'CameraProfile', 'ProcessVersion',
    -- Crop: part of the neutral-render style key on the App side (a crop
    -- changes thumbnail rendering → the anchor must be recalculated).
    'CropLeft', 'CropRight', 'CropTop', 'CropBottom', 'CropAngle',
    -- HSL — 8 bands × {Hue, Saturation, Luminance} (SDK names).
    'HueAdjustmentRed', 'HueAdjustmentOrange', 'HueAdjustmentYellow',
    'HueAdjustmentGreen', 'HueAdjustmentAqua', 'HueAdjustmentBlue',
    'HueAdjustmentPurple', 'HueAdjustmentMagenta',
    'SaturationAdjustmentRed', 'SaturationAdjustmentOrange', 'SaturationAdjustmentYellow',
    'SaturationAdjustmentGreen', 'SaturationAdjustmentAqua', 'SaturationAdjustmentBlue',
    'SaturationAdjustmentPurple', 'SaturationAdjustmentMagenta',
    'LuminanceAdjustmentRed', 'LuminanceAdjustmentOrange', 'LuminanceAdjustmentYellow',
    'LuminanceAdjustmentGreen', 'LuminanceAdjustmentAqua', 'LuminanceAdjustmentBlue',
    'LuminanceAdjustmentPurple', 'LuminanceAdjustmentMagenta',
    -- Camera calibration: transplanted via k-NN from the seeds (core.autocorrect axis
    -- "calib") — not neutralized by the probe, part of hash_style on the App side.
    'EnableCalibration', 'ShadowTint',
    'RedHue', 'RedSaturation', 'GreenHue', 'GreenSaturation', 'BlueHue', 'BlueSaturation',
    -- Style not neutralized by the probe: part of hash_style on the App side
    -- (Fable 5 review DB-01). Hybrid Color Grading names: shadows/HL Hue+Sat =
    -- SplitToning*, the rest ColorGrade* (see lr15_sdk_api_reference §Color Grading).
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
    -- Point curve: number tables (serialized as-is in JSON).
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

-- JSON-serializable table for one photo (snake_case keys, SDK PascalCase develop).
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

-- Returns a JSON array (Json.array) of the selected photos (active target).
function PhotoData.getSelectedPhotos()
    local catalog     = LrApplication.activeCatalog()
    local catalogPath = catalog:getPath()  -- path of the .lrcat → locates the .lrdata
    return photosToArray(catalog:getTargetPhotos(), catalogPath)
end

-- Returns a JSON array of ALL photos in the active catalog (App index).
function PhotoData.getAllPhotos()
    local catalog     = LrApplication.activeCatalog()
    local catalogPath = catalog:getPath()
    return photosToArray(catalog:getAllPhotos(), catalogPath)
end

return PhotoData
