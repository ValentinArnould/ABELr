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
-- Sans tour de boucle depuis ce délai, le pont est considéré mort (contexte tué
-- sans cleanup, erreur fatale…) et peut être relancé.
local HEARTBEAT_TIMEOUT = 5

-- Vrai uniquement si une boucle a effectivement tourné récemment.
-- Ne pas se fier au seul flag : il peut rester bloqué à true si le contexte de
-- la tâche est détruit sans déclencher le cleanup handler.
local function bridgeAlive()
    if not _G.LR_AUTOMATION_BRIDGE_RUNNING then
        return false
    end
    local hb = _G.LR_AUTOMATION_BRIDGE_HEARTBEAT or 0
    return (os.time() - hb) < HEARTBEAT_TIMEOUT
end

-- Exécute un job, retourne la table résultat à renvoyer à l'App.
local function dispatch(job)
    local jobId = job.job_id
    local jobType = job.type

    if jobType == 'test' then
        -- Popup de test : affichée hors boucle pour ne pas bloquer le polling.
        LrTasks.startAsyncTask(function() Utils.test() end)
        return {
            job_id = jobId,
            status = 'ok',
            photos = Json.array({}),
        }
    elseif jobType == 'get_selected_photos' then
        return {
            job_id = jobId,
            status = 'ok',
            photos = PhotoData.getSelectedPhotos(),
        }
    elseif jobType == 'get_catalog_photos' then
        return {
            job_id = jobId,
            status = 'ok',
            photos = PhotoData.getAllPhotos(),
        }
    elseif jobType == 'apply_adjustments' then
        local payload = job.payload or {}
        local adjustments = payload.adjustments or {}
        local report = Adjustments.apply(adjustments)
        local status = (report.applied > 0 or report.total == 0) and 'ok' or 'error'
        local errMsg = nil
        if status == 'error' then
            errMsg = string.format('0/%d appliqués (%d matchés). %s',
                report.total, report.matched,
                report.errors[1] or 'aucune photo de la sélection ne correspond')
        end
        return {
            job_id  = jobId,
            status  = status,
            error   = errMsg,
            applied = report.applied,
            matched = report.matched,
            total   = report.total,
            photos  = Json.array({}),
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

    local ok, result = LrTasks.pcall(dispatch, job)
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

    local encOk, payload = LrTasks.pcall(Json.encode, result)
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

-- Démarre le pont. Idempotent : relance seulement si aucune boucle vivante.
-- Si le flag est resté true mais le heartbeat est périmé (boucle morte), on
-- repart proprement au lieu de refuser de démarrer.
function PollingLoop.start()
    if bridgeAlive() then
        return false   -- déjà une boucle qui tourne
    end

    _G.LR_AUTOMATION_BRIDGE_RUNNING = true
    _G.LR_AUTOMATION_BRIDGE_HEARTBEAT = os.time()

    LrFunctionContext.postAsyncTaskWithContext('LrAutomationBridge', function(context)
        context:addCleanupHandler(function()
            _G.LR_AUTOMATION_BRIDGE_RUNNING = false
            Utils.logf('Pont arrêté.')
        end)
        Utils.logf('Pont démarré → %s', HttpClient.BASE_URL)

        while _G.LR_AUTOMATION_BRIDGE_RUNNING do
            _G.LR_AUTOMATION_BRIDGE_HEARTBEAT = os.time()   -- battement de cœur
            local ok, err = LrTasks.pcall(pollOnce)
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
    return bridgeAlive()
end

return PollingLoop
