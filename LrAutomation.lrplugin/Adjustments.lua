--[[
    Adjustments.lua — application des ajustements develop via SDK.

    Toute écriture passe par catalog:withWriteAccessDo. Les photos sont retrouvées
    par uuid parmi la sélection courante (mapping simple v1).

    Retourne un rapport détaillé (applied / matched / total + erreurs) pour
    diagnostiquer côté App : un uuid non trouvé ou une exception applyDevelopSettings
    n'est plus silencieux.
]]

local LrApplication = import 'LrApplication'
local LrTasks       = import 'LrTasks'
local Utils         = require 'Utils'

local Adjustments = {}

-- Taille des lots d'écriture : une transaction withWriteAccessDo par lot (et non
-- une pour toute la sélection). Borne la durée de chaque transaction (revue Fable 5
-- B-05 : à 500+ photos, une transaction unique dépassait le timeout GUI de 180 s)
-- et permet de rafraîchir le heartbeat entre deux lots (L-05).
local APPLY_CHUNK = 50

-- Compte les clés d'une table (diagnostic).
local function countKeys(t)
    local n = 0
    if type(t) == 'table' then for _ in pairs(t) do n = n + 1 end end
    return n
end

-- adjustments : liste de { photo_id = uuid, develop = { PascalCase = valeur } }.
-- Retourne une table { applied, matched, total, errors = {..} }.
function Adjustments.apply(adjustments)
    local catalog = LrApplication.activeCatalog()

    -- Index uuid → photo sur la sélection courante.
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

    Utils.logf('Adjustments.apply : %d ajustements reçus, %d photos sélectionnées',
        total, selCount)

    -- Diagnostic sur le 1er ajustement : forme des données reçues.
    if total > 0 then
        local a = adjustments[1]
        Utils.logf('  ex. adj[1] photo_id=%s develop(%d clés)=%s',
            tostring(a and a.photo_id), countKeys(a and a.develop),
            a and a.develop and Utils.dumpKeys(a.develop) or 'nil')
    end

    -- Application par LOTS : une transaction par tranche de APPLY_CHUNK photos.
    -- Entre deux lots : heartbeat rafraîchi (le pont ne passe plus pour mort
    -- pendant un gros apply) et main rendue à Lr.
    for base = 1, total, APPLY_CHUNK do
        local hi = math.min(base + APPLY_CHUNK - 1, total)
        catalog:withWriteAccessDo('Lr Automation : ajustements', function()
            for i = base, hi do
                local adj = adjustments[i]
                local photo = byUuid[adj.photo_id]
                if not photo then
                    -- Repli hors sélection (revue Fable 5 L-09, même logique que
                    -- Thumbnails.fetchProbe) : la sélection peut avoir changé entre
                    -- la mesure et l'apply.
                    photo = catalog:findPhotoByUuid(adj.photo_id)
                end
                if not photo then
                    errors[#errors + 1] = 'uuid introuvable : ' .. tostring(adj.photo_id)
                elseif not adj.develop or countKeys(adj.develop) == 0 then
                    errors[#errors + 1] = 'develop vide pour ' .. tostring(adj.photo_id)
                else
                    matched = matched + 1
                    -- LrTasks.pcall (et non pcall standard) : applyDevelopSettings peut
                    -- céder la main (yield) en interne ; yielder à travers le pcall C de
                    -- Lua 5.1 lève « Yielding is not allowed within a C or metamethod call ».
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
        _G.LR_AUTOMATION_BRIDGE_HEARTBEAT = os.time()
        LrTasks.yield()
    end

    Utils.logf('Adjustments.apply : %d/%d appliqués (%d matchés), %d erreur(s)',
        applied, total, matched, #errors)
    for i = 1, math.min(#errors, 5) do
        Utils.logf('  erreur: %s', errors[i])
    end

    return { applied = applied, matched = matched, total = total, errors = errors }
end

return Adjustments
