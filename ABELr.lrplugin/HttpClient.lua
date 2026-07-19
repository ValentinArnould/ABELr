--[[
    HttpClient.lua — LrHttp wrappers (GET/POST JSON) to the Python App.

    LrHttp only works inside an async task. GET: any startAsyncTask.
    POST: must run inside LrFunctionContext.postAsyncTaskWithContext (see PollingLoop).
]]

local LrHttp = import 'LrHttp'
local Json   = require 'Json'
local Utils  = require 'Utils'

local HttpClient = {}

HttpClient.BASE_URL = 'http://127.0.0.1:5000'

local JSON_HEADER = { { field = 'Content-Type', value = 'application/json' } }

-- GET → returns (decodedTable | nil, status | nil, rawBody | nil).
-- status nil = no connection (App is off).
function HttpClient.get(path, timeout)
    local url = HttpClient.BASE_URL .. path
    local body, headers = LrHttp.get(url, {}, timeout or 5)
    if not headers then
        return nil, nil, nil   -- network error / App not started
    end
    local status = headers.status
    if not body or body == '' then
        return nil, status, body
    end
    local decoded = Json.decode(body)
    return decoded, status, body
end

-- POST JSON → returns (decodedTable | nil, status | nil).
-- Must be called from a postAsyncTaskWithContext context.
function HttpClient.postJson(path, tableBody, timeout)
    return HttpClient.postJsonRaw(path, Json.encode(tableBody), timeout)
end

-- Variant: payload already serialized (JSON string).
function HttpClient.postJsonRaw(path, payload, timeout)
    local url = HttpClient.BASE_URL .. path
    local body, headers = LrHttp.post(url, payload, JSON_HEADER, 'POST', timeout or 10)
    if not headers then
        return nil, nil
    end
    local decoded = body and body ~= '' and Json.decode(body) or nil
    return decoded, headers.status
end

-- Quick healthcheck: true if the App responds 200 on /health.
function HttpClient.isAlive()
    local _, status = HttpClient.get('/health', 2)
    return status == 200
end

return HttpClient
