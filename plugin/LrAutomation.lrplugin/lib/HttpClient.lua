--[[
    HttpClient.lua — wrappers LrHttp (GET/POST JSON) vers l'App Python.

    LrHttp ne fonctionne que dans une tâche async. GET : toute startAsyncTask.
    POST : doit tourner dans LrFunctionContext.postAsyncTaskWithContext (cf. PollingLoop).
]]

local LrHttp = import 'LrHttp'
local Json   = require 'lib.Json'
local Utils  = require 'lib.Utils'

local HttpClient = {}

HttpClient.BASE_URL = 'http://127.0.0.1:5000'

local JSON_HEADER = { { field = 'Content-Type', value = 'application/json' } }

-- GET → retourne (decodedTable | nil, status | nil, rawBody | nil).
-- status nil = pas de connexion (App éteinte).
function HttpClient.get(path, timeout)
    local url = HttpClient.BASE_URL .. path
    local body, headers = LrHttp.get(url, {}, timeout or 5)
    if not headers then
        return nil, nil, nil   -- erreur réseau / App non démarrée
    end
    local status = headers.status
    if not body or body == '' then
        return nil, status, body
    end
    local decoded = Json.decode(body)
    return decoded, status, body
end

-- POST JSON → retourne (decodedTable | nil, status | nil).
-- À appeler depuis un contexte postAsyncTaskWithContext.
function HttpClient.postJson(path, tableBody, timeout)
    local url = HttpClient.BASE_URL .. path
    local payload = Json.encode(tableBody)
    local body, headers = LrHttp.post(url, payload, JSON_HEADER, 'POST', timeout or 10)
    if not headers then
        return nil, nil
    end
    local decoded = body and body ~= '' and Json.decode(body) or nil
    return decoded, headers.status
end

-- Healthcheck rapide : true si l'App répond 200 sur /health.
function HttpClient.isAlive()
    local _, status = HttpClient.get('/health', 2)
    return status == 200
end

return HttpClient
