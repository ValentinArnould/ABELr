--[[
    PollingLoop.lua — pont plugin ↔ App.

    Boucle async : GET /jobs/pending toutes les 300ms, exécute le job via SDK,
    POST le résultat sur /jobs/{id}/result. Tourne dans postAsyncTaskWithContext
    (requis par LrHttp.post). Reconnecte automatiquement si l'App redémarre.

    Garde anti-doublon via flag global : un seul pont actif par session Lr.
]]

local LrTasks           = import 'LrTasks'
local LrFunctionContext = import 'LrFunctionContext'

local HttpClient = require 'HttpClient'
local PhotoData  = require 'PhotoData'
local Adjustments= require 'Adjustments'
local Json       = require 'Json'
local Utils      = require 'Utils'

local PollingLoop = {}

local POLL_INTERVAL = 0.3

-- Exécute un job, retourne la table résultat à renvoyer à l'App.
local function dispatch(job)
    local jobId = job.job_id
    local jobType = job.type

    if jobType == 'get_selected_photos' then
        return {
            job_id = jobId,
            status = 'ok',
            photos = PhotoData.getSelectedPhotos(),
        }
    elseif jobType == 'apply_adjustments' then
        local payload = job.payload or {}
        local adjustments = payload.adjustments or {}
        local applied, total = Adjustments.apply(adjustments)
        Utils.logf('apply_adjustments : %d/%d appliqués', applied, total)
        return {
            job_id = jobId,
            status = 'ok',
            photos = Json.array({}),
        }
    end

    return {
        job_id = jobId,
        status = 'error',
        error  = 'type de job inconnu : ' .. tostring(jobType),
        photos = Json.array({}),
    }
end

local function pollOnce()
    local job, status = HttpClient.get('/jobs/pending', 5)
    if status == nil then
        return false   -- App non démarrée : on réessaiera
    end
    if status == 204 or job == nil then
        return true    -- connecté, pas de job
    end

    Utils.logf('Job reçu : type=%s id=%s', tostring(job.type), tostring(job.job_id))

    local ok, result = pcall(dispatch, job)
    if not ok then
        Utils.logf('Erreur dispatch : %s', tostring(result))
        result = {
            job_id = job.job_id,
            status = 'error',
            error  = tostring(result),
            photos = Json.array({}),
        }
    else
        Utils.logf('Dispatch OK : %d photo(s)', type(result.photos) == 'table' and #result.photos or -1)
    end

    local encOk, payload = pcall(Json.encode, result)
    if not encOk then
        Utils.logf('Erreur Json.encode : %s', tostring(payload))
        payload = Json.encode({
            job_id = job.job_id,
            status = 'error',
            error  = 'encode failed: ' .. tostring(payload),
            photos = Json.array({}),
        })
    end

    local _, postStatus = HttpClient.postJsonRaw('/jobs/' .. job.job_id .. '/result', payload, 10)
    Utils.logf('POST result → HTTP %s', tostring(postStatus))
    return true
end

-- Démarre le pont. Idempotent : ne lance qu'une boucle par session.
function PollingLoop.start()
    if _G.LR_AUTOMATION_BRIDGE_RUNNING then
        return false
    end
    _G.LR_AUTOMATION_BRIDGE_RUNNING = true

    LrFunctionContext.postAsyncTaskWithContext('LrAutomationBridge', function(context)
        context:addCleanupHandler(function()
            _G.LR_AUTOMATION_BRIDGE_RUNNING = false
            Utils.logf('Pont arrêté.')
        end)
        Utils.logf('Pont démarré → %s', HttpClient.BASE_URL)

        while _G.LR_AUTOMATION_BRIDGE_RUNNING do
            local ok, err = pcall(pollOnce)
            if not ok then
                Utils.logf('Erreur boucle : %s', tostring(err))
            end
            LrTasks.sleep(POLL_INTERVAL)
        end
    end)
    return true
end

function PollingLoop.stop()
    _G.LR_AUTOMATION_BRIDGE_RUNNING = false
end

function PollingLoop.isRunning()
    return _G.LR_AUTOMATION_BRIDGE_RUNNING == true
end

return PollingLoop
