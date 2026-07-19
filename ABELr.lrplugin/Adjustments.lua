--[[
    Adjustments.lua — applies develop adjustments via the SDK.

    Every write goes through catalog:withWriteAccessDo. Photos are looked up
    by uuid among the current selection (simple v1 mapping).

    Returns a detailed report (applied / matched / total + errors) for
    diagnosing on the App side: an uuid not found or an applyDevelopSettings
    exception is no longer silent.
]]

local LrApplication = import 'LrApplication'
local LrTasks       = import 'LrTasks'
local Utils         = require 'Utils'

local Adjustments = {}

-- Write batch size: one withWriteAccessDo transaction per batch (rather than
-- a single one for the whole selection). Bounds the duration of each transaction
-- (Fable 5 review B-05: with 500+ photos, a single transaction exceeded the
-- 180s GUI timeout) and allows refreshing the heartbeat between batches (L-05).
local APPLY_CHUNK = 50

-- Counts the keys of a table (diagnostic).
local function countKeys(t)
    local n = 0
    if type(t) == 'table' then for _ in pairs(t) do n = n + 1 end end
    return n
end

-- adjustments: list of { photo_id = uuid, develop = { PascalCase = value } }.
-- Returns a table { applied, matched, total, errors = {..} }.
function Adjustments.apply(adjustments)
    local catalog = LrApplication.activeCatalog()

    -- Index uuid → photo over the current selection.
    local byUuid = {}
    local selCount = 0
    for _, photo in ipairs(catalog:getTargetPhotos()) do
        byUuid[photo:getRawMetadata('uuid')] = photo
        selCount = selCount + 1
    end

    local total   = #adjustments
    local matched = 0
    local applied = 0
    local errors  = {}

    Utils.logf('Adjustments.apply: %d adjustments received, %d photos selected',
        total, selCount)

    -- Diagnostic on the 1st adjustment: shape of the data received.
    if total > 0 then
        local a = adjustments[1]
        Utils.logf('  e.g. adj[1] photo_id=%s develop(%d keys)=%s',
            tostring(a and a.photo_id), countKeys(a and a.develop),
            a and a.develop and Utils.dumpKeys(a.develop) or 'nil')
    end

    -- Applying in BATCHES: one transaction per slice of APPLY_CHUNK photos.
    -- Between two batches: heartbeat refreshed (the bridge no longer appears
    -- dead during a large apply) and control yielded back to Lr.
    for base = 1, total, APPLY_CHUNK do
        local hi = math.min(base + APPLY_CHUNK - 1, total)
        catalog:withWriteAccessDo('ABELr: adjustments', function()
            for i = base, hi do
                local adj = adjustments[i]
                local photo = byUuid[adj.photo_id]
                if not photo then
                    -- Fallback outside the selection (Fable 5 review L-09, same logic
                    -- as Thumbnails.fetchProbe): the selection may have changed between
                    -- the measurement and the apply.
                    photo = catalog:findPhotoByUuid(adj.photo_id)
                end
                if not photo then
                    errors[#errors + 1] = 'uuid not found: ' .. tostring(adj.photo_id)
                elseif not adj.develop or countKeys(adj.develop) == 0 then
                    errors[#errors + 1] = 'empty develop for ' .. tostring(adj.photo_id)
                else
                    matched = matched + 1
                    -- LrTasks.pcall (not standard pcall): applyDevelopSettings can
                    -- yield internally; yielding through Lua 5.1's C pcall
                    -- raises "Yielding is not allowed within a C or metamethod call".
                    local ok, err = LrTasks.pcall(function()
                        photo:applyDevelopSettings(adj.develop)
                    end)
                    if ok then
                        applied = applied + 1
                    else
                        errors[#errors + 1] = 'applyDevelopSettings: ' .. tostring(err)
                    end
                end
            end
        end)
        _G.ABELR_BRIDGE_HEARTBEAT = os.time()
        LrTasks.yield()
    end

    Utils.logf('Adjustments.apply: %d/%d applied (%d matched), %d error(s)',
        applied, total, matched, #errors)
    for i = 1, math.min(#errors, 5) do
        Utils.logf('  error: %s', errors[i])
    end

    return { applied = applied, matched = matched, total = total, errors = errors }
end

return Adjustments
