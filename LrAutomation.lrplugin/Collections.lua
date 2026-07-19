--[[
    Collections.lua — collections & ensembles de collections (jobs Phase 2).

    list   : arbre {name,id,kind,photo_count?,children[]} (lecture, dans une task).
    create : catalog:createCollection(name, parentSet, canReturnPrior) (writeAccess).
    addPhotos : collection:addPhotos(photos) (writeAccess).

    APIs SDK (réf. lr15_sdk_api_reference.md §4) :
      catalog:getChildCollections() / :getChildCollectionSets()          [confirmé]
      catalog:createCollection(name, parentSet, canReturnPrior)          [confirmé]
    ⚠️ Méthodes d'INSTANCE non listées dans la réf (LrCollection/LrCollectionSet) —
       canoniques Adobe, À CONFIRMER au 1er run en Lr :
         obj:getName() · obj.localIdentifier · collection:getPhotos()
         collection:addPhotos(photos) · set:getChildCollections()/getChildCollectionSets()
]]

local LrApplication = import 'LrApplication'
local LrTasks       = import 'LrTasks'
local Json          = require 'Json'
local PhotoLookup   = require 'PhotoLookup'
local Utils         = require 'Utils'

local Collections = {}

-- Nœud d'arbre récursif. kind = 'collection' | 'set'.
local function buildNode(obj, kind)
    local node = {
        name = obj:getName(),
        id = tostring(obj.localIdentifier),
        kind = kind,
        children = Json.array({}),
    }
    if kind == 'collection' then
        local ok, photos = LrTasks.pcall(function() return obj:getPhotos() end)
        if ok and photos then node.photo_count = #photos end
    else
        for _, c in ipairs(obj:getChildCollections()) do
            node.children[#node.children + 1] = buildNode(c, 'collection')
        end
        for _, s in ipairs(obj:getChildCollectionSets()) do
            node.children[#node.children + 1] = buildNode(s, 'set')
        end
    end
    return node
end

-- Arbre complet des collections/ensembles à la racine du catalogue.
function Collections.list()
    local catalog = LrApplication.activeCatalog()
    local out = Json.array({})
    for _, c in ipairs(catalog:getChildCollections()) do
        out[#out + 1] = buildNode(c, 'collection')
    end
    for _, s in ipairs(catalog:getChildCollectionSets()) do
        out[#out + 1] = buildNode(s, 'set')
    end
    return out
end

-- Cherche une collection par id (localIdentifier en string) ou nom, en profondeur.
local function findCollection(catalog, ref)
    local found = nil
    local function walkColls(colls)
        for _, c in ipairs(colls) do
            if tostring(c.localIdentifier) == ref or c:getName() == ref then
                found = c
                return true
            end
        end
        return false
    end
    local function walkSets(sets)
        for _, s in ipairs(sets) do
            if walkColls(s:getChildCollections()) then return true end
            if walkSets(s:getChildCollectionSets()) then return true end
        end
        return false
    end
    if walkColls(catalog:getChildCollections()) then return found end
    if walkSets(catalog:getChildCollectionSets()) then return found end
    return nil
end

-- Cherche un ENSEMBLE (set) par id ou nom (pour le parent de create).
local function findCollectionSet(catalog, ref)
    local found = nil
    local function walk(sets)
        for _, s in ipairs(sets) do
            if tostring(s.localIdentifier) == ref or s:getName() == ref then
                found = s
                return true
            end
            if walk(s:getChildCollectionSets()) then return true end
        end
        return false
    end
    if walk(catalog:getChildCollectionSets()) then return found end
    return nil
end

-- Crée (ou retrouve) une collection. Retourne {name, id, created}.
function Collections.create(name, parent)
    local catalog = LrApplication.activeCatalog()
    local parentSet = nil
    if parent and parent ~= '' then
        parentSet = findCollectionSet(catalog, parent)
        if not parentSet then
            Utils.logf('Collections.create : parent introuvable « %s » → racine', tostring(parent))
        end
    end
    local created = nil
    local ok, err = LrTasks.pcall(function()
        catalog:withWriteAccessDo('Lr Automation : créer collection', function()
            created = catalog:createCollection(name, parentSet, true)  -- canReturnPrior
        end)
    end)
    if not ok or not created then
        return { name = name, id = Json.null, created = false, error = tostring(err) }
    end
    -- Objet accessible après la fin du callback (réf SDK §4).
    Utils.logf('Collections.create : « %s » ok', tostring(name))
    return { name = created:getName(), id = tostring(created.localIdentifier), created = true }
end

-- Ajoute des photos à la collection `ref` (id ou nom). Retourne {applied,total,errors}.
function Collections.addPhotos(ref, photoIds)
    local catalog = LrApplication.activeCatalog()
    local coll = findCollection(catalog, ref)
    if not coll then
        return { applied = 0, total = #photoIds,
                 errors = { 'collection introuvable : ' .. tostring(ref) } }
    end
    local matched, missing = PhotoLookup.resolve(photoIds)
    local errors = {}
    for _, id in ipairs(missing) do
        errors[#errors + 1] = 'uuid introuvable : ' .. tostring(id)
    end
    local photos = {}
    for _, m in ipairs(matched) do photos[#photos + 1] = m.photo end

    local applied = 0
    if #photos > 0 then
        local ok, err = LrTasks.pcall(function()
            catalog:withWriteAccessDo('Lr Automation : ajout collection', function()
                coll:addPhotos(photos)
            end)
        end)
        if ok then
            applied = #photos
        else
            errors[#errors + 1] = 'addPhotos: ' .. tostring(err)
        end
    end
    Utils.logf('Collections.addPhotos : %d/%d ajoutées à « %s »',
        applied, #photoIds, tostring(ref))
    return { applied = applied, total = #photoIds, errors = errors }
end

return Collections
