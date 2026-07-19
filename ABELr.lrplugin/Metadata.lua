--[[
    Metadata.lua — écriture des métadonnées de classement et mots-clés (jobs Phase 2).

    setRating / setFlagColor / setKeywords. Toute écriture dans withWriteAccessDo,
    par lots (heartbeat rafraîchi entre deux lots comme Adjustments.apply). Retourne
    { applied, total, errors } — converti en errors_summary par PollingLoop.batchResult.

    APIs SDK utilisées (réf. lr15_sdk_api_reference.md §5) :
      photo:setRawMetadata('rating'|'pickStatus'|'colorNameForLabel', v)   [confirmé]
      catalog:createKeyword(name, synonyms, includeOnExport, parent, returnExisting) [confirmé]
      photo:addKeyword(kw) / :removeKeyword(kw)                             [confirmé]
    ⚠️ kw:getName() n'est pas listé dans la réf — méthode LrKeyword canonique,
       À CONFIRMER au 1er run en Lr (cf. règle CLAUDE.md sur les méthodes non vérifiées).
]]

local LrApplication = import 'LrApplication'
local LrTasks       = import 'LrTasks'
local PhotoLookup   = require 'PhotoLookup'
local Utils         = require 'Utils'

local Metadata = {}

local CHUNK = 50
local FLAG_TO_PICK = { pick = 1, reject = -1, none = 0 }

-- Ajoute les uuids manquants comme erreurs (non trouvés = non appliqués).
local function pushMissing(errors, missing)
    for _, id in ipairs(missing) do
        errors[#errors + 1] = 'uuid introuvable : ' .. tostring(id)
    end
end

-- Applique `writeFn(photo)` à chaque photo matchée, par lots withWriteAccessDo.
-- Retourne (applied). Les erreurs par photo sont poussées dans `errors`.
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

-- rating : 0-5.
function Metadata.setRating(photoIds, rating)
    local matched, missing = PhotoLookup.resolve(photoIds)
    local errors = {}
    pushMissing(errors, missing)
    Utils.logf('Metadata.setRating : %d/%d matchés, rating=%s',
        #matched, #photoIds, tostring(rating))
    local applied = applyBatched('ABELr : note', matched, errors, function(photo)
        photo:setRawMetadata('rating', rating)
    end)
    return { applied = applied, total = #photoIds, errors = errors }
end

-- flag : 'pick'|'reject'|'none' ou nil ; color : nom couleur / 'none' ou nil.
function Metadata.setFlagColor(photoIds, flag, color)
    local matched, missing = PhotoLookup.resolve(photoIds)
    local errors = {}
    pushMissing(errors, missing)
    -- 'none' → 0 (0 est truthy en Lua, donc bien conservé) ; nil → nil (on ne touche pas).
    local pick = flag and FLAG_TO_PICK[flag] or nil
    Utils.logf('Metadata.setFlagColor : %d/%d matchés, flag=%s color=%s',
        #matched, #photoIds, tostring(flag), tostring(color))
    local applied = applyBatched('ABELr : flag/label', matched, errors, function(photo)
        if pick ~= nil then photo:setRawMetadata('pickStatus', pick) end
        if color ~= nil then photo:setRawMetadata('colorNameForLabel', color) end
    end)
    return { applied = applied, total = #photoIds, errors = errors }
end

-- addNames / removeNames : listes de noms de mots-clés (strings).
function Metadata.setKeywords(photoIds, addNames, removeNames)
    local catalog = LrApplication.activeCatalog()
    local matched, missing = PhotoLookup.resolve(photoIds)
    local errors = {}
    pushMissing(errors, missing)
    addNames = addNames or {}
    removeNames = removeNames or {}

    -- Phase 1 : créer / retrouver les mots-clés à ajouter, dans une transaction
    -- SÉPARÉE — un objet créé dans withWriteAccessDo n'est accessible qu'APRÈS la
    -- fin du callback (réf SDK §4). returnExisting=true → réutilise l'existant.
    local addKw = {}
    if #addNames > 0 then
        catalog:withWriteAccessDo('ABELr : mots-clés', function()
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

    Utils.logf('Metadata.setKeywords : %d/%d matchés, +%d/-%d mots-clés',
        #matched, #photoIds, #addNames, #removeNames)

    -- Phase 2 : appliquer add/remove par lots.
    local applied = applyBatched('ABELr : mots-clés', matched, errors, function(photo)
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
