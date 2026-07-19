--[[
    Metadata.lua — writes rating/flag/keyword metadata (Phase 2 jobs).

    setRating / setFlagColor / setKeywords. All writes inside withWriteAccessDo,
    in batches (heartbeat refreshed between batches, like Adjustments.apply). Returns
    { applied, total, errors } — converted to errors_summary by PollingLoop.batchResult.

    SDK APIs used (ref. lr15_sdk_api_reference.md §5):
      photo:setRawMetadata('rating'|'pickStatus'|'colorNameForLabel', v)   [confirmed]
      catalog:createKeyword(name, synonyms, includeOnExport, parent, returnExisting) [confirmed]
      photo:addKeyword(kw) / :removeKeyword(kw)                             [confirmed]
    ⚠️ kw:getName() is not listed in the reference — canonical LrKeyword method,
       TO CONFIRM on the first live Lr run (cf. CLAUDE.md rule on unverified methods).
]]

local LrApplication = import 'LrApplication'
local LrTasks       = import 'LrTasks'
local PhotoLookup   = require 'PhotoLookup'
local Utils         = require 'Utils'

local Metadata = {}

local CHUNK = 50
local FLAG_TO_PICK = { pick = 1, reject = -1, none = 0 }

-- Adds missing uuids as errors (not found = not applied).
local function pushMissing(errors, missing)
    for _, id in ipairs(missing) do
        errors[#errors + 1] = 'uuid not found: ' .. tostring(id)
    end
end

-- Applies `writeFn(photo)` to each matched photo, in withWriteAccessDo batches.
-- Returns (applied). Per-photo errors are pushed into `errors`.
local function applyBatched(actionName, matched, errors, writeFn)
    local catalog = LrApplication.activeCatalog()
    local applied = 0
    for base = 1, #matched, CHUNK do
        local hi = math.min(base + CHUNK - 1, #matched)
        catalog:withWriteAccessDo(actionName, function()
            for i = base, hi do
                local m = matched[i]
                local ok, err = LrTasks.pcall(function() writeFn(m.photo) end)
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
    return applied
end

-- rating: 0-5.
function Metadata.setRating(photoIds, rating)
    local matched, missing = PhotoLookup.resolve(photoIds)
    local errors = {}
    pushMissing(errors, missing)
    Utils.logf('Metadata.setRating: %d/%d matched, rating=%s',
        #matched, #photoIds, tostring(rating))
    local applied = applyBatched('ABELr: rating', matched, errors, function(photo)
        photo:setRawMetadata('rating', rating)
    end)
    return { applied = applied, total = #photoIds, errors = errors }
end

-- flag: 'pick'|'reject'|'none' or nil; color: color name / 'none' or nil.
function Metadata.setFlagColor(photoIds, flag, color)
    local matched, missing = PhotoLookup.resolve(photoIds)
    local errors = {}
    pushMissing(errors, missing)
    -- 'none' -> 0 (0 is truthy in Lua, so it's preserved correctly); nil -> nil (untouched).
    local pick = flag and FLAG_TO_PICK[flag] or nil
    Utils.logf('Metadata.setFlagColor: %d/%d matched, flag=%s color=%s',
        #matched, #photoIds, tostring(flag), tostring(color))
    local applied = applyBatched('ABELr: flag/label', matched, errors, function(photo)
        if pick ~= nil then photo:setRawMetadata('pickStatus', pick) end
        if color ~= nil then photo:setRawMetadata('colorNameForLabel', color) end
    end)
    return { applied = applied, total = #photoIds, errors = errors }
end

-- addNames / removeNames: lists of keyword names (strings).
function Metadata.setKeywords(photoIds, addNames, removeNames)
    local catalog = LrApplication.activeCatalog()
    local matched, missing = PhotoLookup.resolve(photoIds)
    local errors = {}
    pushMissing(errors, missing)
    addNames = addNames or {}
    removeNames = removeNames or {}

    -- Phase 1: create / find the keywords to add, in a SEPARATE transaction —
    -- an object created inside withWriteAccessDo is only accessible AFTER the
    -- callback ends (SDK ref §4). returnExisting=true -> reuses an existing one.
    local addKw = {}
    if #addNames > 0 then
        catalog:withWriteAccessDo('ABELr: keywords', function()
            for _, name in ipairs(addNames) do
                local ok, kw = LrTasks.pcall(function()
                    return catalog:createKeyword(name, {}, true, nil, true)
                end)
                if ok and kw then
                    addKw[#addKw + 1] = kw
                else
                    errors[#errors + 1] = 'createKeyword ' .. tostring(name) .. ': ' .. tostring(kw)
                end
            end
        end)
    end
    local removeSet = {}
    for _, n in ipairs(removeNames) do removeSet[n] = true end

    Utils.logf('Metadata.setKeywords: %d/%d matched, +%d/-%d keywords',
        #matched, #photoIds, #addNames, #removeNames)

    -- Phase 2: apply add/remove in batches.
    local applied = applyBatched('ABELr: keywords', matched, errors, function(photo)
        for _, kw in ipairs(addKw) do photo:addKeyword(kw) end
        if next(removeSet) then
            for _, kw in ipairs(photo:getRawMetadata('keywords') or {}) do
                if removeSet[kw:getName()] then photo:removeKeyword(kw) end
            end
        end
    end)
    return { applied = applied, total = #photoIds, errors = errors }
end

return Metadata
