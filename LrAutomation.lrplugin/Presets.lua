--[[
    Presets.lua — presets develop (jobs Phase 2).

    list  : énumère les presets de tous les dossiers (lecture, dans une task).
    apply : applique un preset (par uuid ou nom) aux photos, par lots (writeAccess).

    APIs SDK (réf. lr15_sdk_api_reference.md §3/§5) :
      LrApplication.developPresetFolders()                               [confirmé]
      LrApplication.developPresetByUuid(uuid)                            [confirmé]
      photo:applyDevelopPreset(preset, _PLUGIN, presetAmount, updateAI)  [confirmé]
    ⚠️ Méthodes d'instance non listées dans la réf — canoniques Adobe, À CONFIRMER
       au 1er run en Lr :
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

-- Liste { {name, uuid, folder}, ... } de tous les presets develop.
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

-- Résout un preset par uuid (rapide) puis par nom (repli).
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

-- Applique `presetRef` (uuid ou nom) aux photos. Retourne {applied,total,errors}.
function Presets.apply(photoIds, presetRef)
    local preset = resolvePreset(presetRef)
    if not preset then
        return { applied = 0, total = #photoIds,
                 errors = { 'preset introuvable : ' .. tostring(presetRef) } }
    end
    local matched, missing = PhotoLookup.resolve(photoIds)
    local errors = {}
    for _, id in ipairs(missing) do
        errors[#errors + 1] = 'uuid introuvable : ' .. tostring(id)
    end
    local catalog = LrApplication.activeCatalog()
    local applied = 0
    for base = 1, #matched, CHUNK do
        local hi = math.min(base + CHUNK - 1, #matched)
        catalog:withWriteAccessDo('Lr Automation : preset develop', function()
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
        _G.LR_AUTOMATION_BRIDGE_HEARTBEAT = os.time()
        LrTasks.yield()
    end
    Utils.logf('Presets.apply : %d/%d appliqués (preset=%s)',
        applied, #photoIds, tostring(presetRef))
    return { applied = applied, total = #photoIds, errors = errors }
end

return Presets
