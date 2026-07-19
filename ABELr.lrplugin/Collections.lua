--[[
    Collections.lua — collections & collection sets (Phase 2 jobs).

    list   : tree {name,id,kind,photo_count?,children[]} (read, inside a task).
    create : catalog:createCollection(name, parentSet, canReturnPrior) (writeAccess).
    addPhotos : collection:addPhotos(photos) (writeAccess).

    SDK APIs (ref. lr15_sdk_api_reference.md §4):
      catalog:getChildCollections() / :getChildCollectionSets()          [confirmed]
      catalog:createCollection(name, parentSet, canReturnPrior)          [confirmed]
    ⚠️ INSTANCE methods not listed in the reference (LrCollection/LrCollectionSet) —
       canonical Adobe methods, TO CONFIRM on first live Lr run:
         obj:getName() · obj.localIdentifier · collection:getPhotos()
         collection:addPhotos(photos) · set:getChildCollections()/getChildCollectionSets()
]]

local LrApplication = import 'LrApplication'
local LrTasks       = import 'LrTasks'
local Json          = require 'Json'
local PhotoLookup   = require 'PhotoLookup'
local Utils         = require 'Utils'

local Collections = {}

-- Recursive tree node. kind = 'collection' | 'set'.
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

-- Full tree of collections/sets at the catalog root.
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

-- Finds a collection by id (localIdentifier as string) or name, recursively.
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

-- Finds a SET by id or name (for create's parent).
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

-- Creates (or retrieves) a collection. Returns {name, id, created}.
function Collections.create(name, parent)
    local catalog = LrApplication.activeCatalog()
    local parentSet = nil
    if parent and parent ~= '' then
        parentSet = findCollectionSet(catalog, parent)
        if not parentSet then
            Utils.logf('Collections.create: parent not found "%s" → root', tostring(parent))
        end
    end
    local created = nil
    local ok, err = LrTasks.pcall(function()
        catalog:withWriteAccessDo('ABELr: create collection', function()
            created = catalog:createCollection(name, parentSet, true)  -- canReturnPrior
        end)
    end)
    if not ok or not created then
        return { name = name, id = Json.null, created = false, error = tostring(err) }
    end
    -- Object accessible after the callback returns (SDK ref §4).
    Utils.logf('Collections.create: "%s" ok', tostring(name))
    return { name = created:getName(), id = tostring(created.localIdentifier), created = true }
end

-- Adds photos to the collection `ref` (id or name). Returns {applied,total,errors}.
function Collections.addPhotos(ref, photoIds)
    local catalog = LrApplication.activeCatalog()
    local coll = findCollection(catalog, ref)
    if not coll then
        return { applied = 0, total = #photoIds,
                 errors = { 'collection not found: ' .. tostring(ref) } }
    end
    local matched, missing = PhotoLookup.resolve(photoIds)
    local errors = {}
    for _, id in ipairs(missing) do
        errors[#errors + 1] = 'uuid not found: ' .. tostring(id)
    end
    local photos = {}
    for _, m in ipairs(matched) do photos[#photos + 1] = m.photo end

    local applied = 0
    if #photos > 0 then
        local ok, err = LrTasks.pcall(function()
            catalog:withWriteAccessDo('ABELr: add to collection', function()
                coll:addPhotos(photos)
            end)
        end)
        if ok then
            applied = #photos
        else
            errors[#errors + 1] = 'addPhotos: ' .. tostring(err)
        end
    end
    Utils.logf('Collections.addPhotos: %d/%d added to "%s"',
        applied, #photoIds, tostring(ref))
    return { applied = applied, total = #photoIds, errors = errors }
end

return Collections
