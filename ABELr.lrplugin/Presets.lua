--[[
    Presets.lua — develop presets (Phase 2 jobs).

    list  : enumerates presets from all folders (read, inside a task).
    apply : applies a preset (by uuid or name) to photos, in batches (writeAccess).

    SDK APIs (ref. lr15_sdk_api_reference.md §3/§5):
      LrApplication.developPresetFolders()                               [confirmed]
      LrApplication.developPresetByUuid(uuid)                            [confirmed]
      photo:applyDevelopPreset(preset, _PLUGIN, presetAmount, updateAI)  [confirmed]
    ⚠️ Instance methods not listed in the reference — canonical Adobe, TO CONFIRM
       on first run in Lr:
         folder:getName() · folder:getDevelopPresets()
         preset:getName() · preset:getUuid()
]]

local LrApplication = import 'LrApplication'
local LrTasks       = import 'LrTasks'
local Json          = require 'Json'
local PhotoLookup   = require 'PhotoLookup'
local Utils         = require 'Utils'

local Presets = {}

local CHUNK = 50

-- List of { {name, uuid, folder}, ... } for all develop presets.
function Presets.list()
    local out = Json.array({})
    for _, folder in ipairs(LrApplication.developPresetFolders()) do
        local fname = folder:getName()
        for _, preset in ipairs(folder:getDevelopPresets()) do
            out[#out + 1] = {
                name = preset:getName(),
                uuid = preset:getUuid(),
                folder = fname,
            }
        end
    end
    return out
end

-- Resolves a preset by uuid (fast) then by name (fallback).
local function resolvePreset(ref)
    local ok, preset = LrTasks.pcall(function()
        return LrApplication.developPresetByUuid(ref)
    end)
    if ok and preset then return preset end
    for _, folder in ipairs(LrApplication.developPresetFolders()) do
        for _, p in ipairs(folder:getDevelopPresets()) do
            if p:getName() == ref then return p end
        end
    end
    return nil
end

-- Applies `presetRef` (uuid or name) to the photos. Returns {applied,total,errors}.
function Presets.apply(photoIds, presetRef)
    local preset = resolvePreset(presetRef)
    if not preset then
        return { applied = 0, total = #photoIds,
                 errors = { 'preset not found: ' .. tostring(presetRef) } }
    end
    local matched, missing = PhotoLookup.resolve(photoIds)
    local errors = {}
    for _, id in ipairs(missing) do
        errors[#errors + 1] = 'uuid not found: ' .. tostring(id)
    end
    local catalog = LrApplication.activeCatalog()
    local applied = 0
    for base = 1, #matched, CHUNK do
        local hi = math.min(base + CHUNK - 1, #matched)
        catalog:withWriteAccessDo('ABELr: develop preset', function()
            for i = base, hi do
                local m = matched[i]
                local ok, err = LrTasks.pcall(function()
                    m.photo:applyDevelopPreset(preset, _PLUGIN)
                end)
                if ok then
                    applied = applied + 1
                else
                    errors[#errors + 1] = tostring(m.id) .. ': ' .. tostring(err)
                end
            end
        end)
        _G.ABELR_BRIDGE_HEARTBEAT = os.time()
        LrTasks.yield()
    end
    Utils.logf('Presets.apply: %d/%d applied (preset=%s)',
        applied, #photoIds, tostring(presetRef))
    return { applied = applied, total = #photoIds, errors = errors }
end

return Presets
