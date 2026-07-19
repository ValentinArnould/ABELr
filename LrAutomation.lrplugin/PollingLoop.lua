--[[
    PollingLoop.lua — pont plugin ↔ App.

    Boucle async : GET /jobs/pending toutes les 300ms, exécute le job via SDK,
    POST le résultat sur /jobs/{id}/result. Tourne dans postAsyncTaskWithContext
    (requis par LrHttp.post). Reconnecte automatiquement si l'App redémarre.

    Garde anti-doublon via flag global : un seul pont actif par session Lr.

    Hot-reload : `dispatch` est stocké dans _G.LR_AUTOMATION_DISPATCH et mis à jour
    à chaque rechargement du module. La boucle en cours (`pollOnce`) l'appelle via
    le global — elle récupère automatiquement le nouveau code sans redémarrage.
]]

local LrApplication     = import 'LrApplication'
local LrTasks           = import 'LrTasks'
local LrFunctionContext = import 'LrFunctionContext'

local HttpClient = require 'HttpClient'
local PhotoData  = require 'PhotoData'
local Adjustments= require 'Adjustments'
local Thumbnails = require 'Thumbnails'
local Metadata   = require 'Metadata'
local Collections= require 'Collections'
local Presets    = require 'Presets'
local Json       = require 'Json'
local Utils      = require 'Utils'

local PollingLoop = {}

local POLL_INTERVAL = 0.3
-- Sans tour de boucle depuis ce délai, le pont est considéré mort (contexte tué
-- sans cleanup, erreur fatale…) et peut être relancé.
local HEARTBEAT_TIMEOUT = 5

-- Le pont est « vivant » si une boucle a pollé récemment (heartbeat frais).
-- On ne se fie PAS à un flag booléen partagé : une boucle qui meurt ne doit
-- jamais pouvoir éteindre une boucle plus récente. Le heartbeat seul est la
-- source de vérité — il devient périmé tout seul si plus aucune boucle ne tourne.
local function bridgeAlive()
    local hb = _G.LR_AUTOMATION_BRIDGE_HEARTBEAT or 0
    return (os.time() - hb) < HEARTBEAT_TIMEOUT
end

-- Construit la table résultat standard d'un job batch (set_rating, set_keywords,
-- add_to_collection, apply_develop_preset…) à partir d'un rapport
-- { applied, total, errors }. Même logique errors_summary que apply_adjustments
-- (revue Fable 5 L-04) : un traitement PARTIEL ne perd pas les causes.
local function batchResult(jobId, report)
    local total   = report.total or 0
    local applied = report.applied or 0
    local errors  = report.errors or {}
    local status  = (applied > 0 or total == 0) and 'ok' or 'error'
    local errorsSummary = nil
    if #errors > 0 then
        local parts = {}
        for i = 1, math.min(5, #errors) do parts[#parts + 1] = errors[i] end
        errorsSummary = table.concat(parts, ' | ')
        if #errors > 5 then
            errorsSummary = errorsSummary .. string.format(' | +%d autres', #errors - 5)
        end
    end
    local errMsg = nil
    if status == 'error' then
        errMsg = string.format('0/%d appliqué(s). %s',
            total, errors[1] or 'aucune photo ne correspond')
    end
    return {
        job_id  = jobId,
        status  = status,
        error   = errMsg,
        errors_summary = errorsSummary,
        applied = applied,
        total   = total,
        photos  = Json.array({}),
    }
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
    elseif jobType == 'get_thumbnails' then
        local payload  = job.payload or {}
        local width    = payload.width  or 512
        local height   = payload.height or 512
        -- Utilise la sélection courante (la même liste que get_selected_photos).
        local catalog  = LrApplication.activeCatalog()
        local photos   = catalog:getTargetPhotos()
        local thumbs   = Thumbnails.fetch(photos, width, height)
        -- Filtre optionnel : si payload.photo_ids fourni, ne retourner que ceux-là.
        local filter   = {}
        if payload.photo_ids and #payload.photo_ids > 0 then
            for _, id in ipairs(payload.photo_ids) do filter[id] = true end
        end
        local out = Json.array({})
        for _, t in ipairs(thumbs) do
            if not payload.photo_ids or #payload.photo_ids == 0 or filter[t.photo_id] then
                out[#out + 1] = {
                    photo_id       = t.photo_id,
                    thumbnail_path = t.thumbnail_path,
                    error          = t.error,
                }
            end
        end
        return {
            job_id     = jobId,
            status     = 'ok',
            thumbnails = out,
            photos     = Json.array({}),
        }
    elseif jobType == 'render_probe' then
        -- Rendu sondé : applique des réglages temporaires, rend la miniature, restaure.
        -- Sert au calage de la réponse ∂rendu/∂curseur et au rendu neutre d'ancrage.
        local payload     = job.payload or {}
        local adjustments = payload.adjustments or {}
        local width       = payload.width  or 512
        local height      = payload.height or 512
        local settle      = payload.settle
        local thumbs      = Thumbnails.fetchProbe(adjustments, width, height, settle)
        local out = Json.array({})
        for _, t in ipairs(thumbs) do
            out[#out + 1] = {
                photo_id       = t.photo_id,
                thumbnail_path = t.thumbnail_path,
                error          = t.error,
                asshot_temp    = t.asshot_temp,
                asshot_tint    = t.asshot_tint,
                restore_error  = t.restore_error,
            }
        end
        return {
            job_id     = jobId,
            status     = 'ok',
            thumbnails = out,
            photos     = Json.array({}),
        }
    elseif jobType == 'apply_adjustments' then
        local payload = job.payload or {}
        local adjustments = payload.adjustments or {}
        local report = Adjustments.apply(adjustments)
        local status = (report.applied > 0 or report.total == 0) and 'ok' or 'error'
        -- Résumé d'erreurs TOUJOURS joint quand il y en a (revue Fable 5 L-04) :
        -- un apply PARTIEL (status='ok' avec des échecs) ne perd plus les causes.
        local errorsSummary = nil
        if #report.errors > 0 then
            local parts = {}
            for i = 1, math.min(5, #report.errors) do parts[#parts + 1] = report.errors[i] end
            errorsSummary = table.concat(parts, ' | ')
            if #report.errors > 5 then
                errorsSummary = errorsSummary .. string.format(' | +%d autres', #report.errors - 5)
            end
        end
        local errMsg = nil
        if status == 'error' then
            errMsg = string.format('0/%d appliqués (%d matchés). %s',
                report.total, report.matched,
                report.errors[1] or 'aucune photo ne correspond')
        end
        return {
            job_id  = jobId,
            status  = status,
            error   = errMsg,
            errors_summary = errorsSummary,
            applied = report.applied,
            matched = report.matched,
            total   = report.total,
            photos  = Json.array({}),
        }

    -- ------------------------------------------------------------------ --
    -- Phase 2 : notes / flags / mots-clés / collections / presets develop
    -- ------------------------------------------------------------------ --
    elseif jobType == 'set_rating' then
        local p = job.payload or {}
        return batchResult(jobId, Metadata.setRating(p.photo_ids or {}, p.rating))
    elseif jobType == 'set_flag_color' then
        local p = job.payload or {}
        return batchResult(jobId, Metadata.setFlagColor(p.photo_ids or {}, p.flag, p.color))
    elseif jobType == 'set_keywords' then
        local p = job.payload or {}
        return batchResult(jobId, Metadata.setKeywords(p.photo_ids or {}, p.add or {}, p.remove or {}))
    elseif jobType == 'add_to_collection' then
        local p = job.payload or {}
        return batchResult(jobId, Collections.addPhotos(p.collection, p.photo_ids or {}))
    elseif jobType == 'apply_develop_preset' then
        local p = job.payload or {}
        return batchResult(jobId, Presets.apply(p.photo_ids or {}, p.preset))
    elseif jobType == 'list_collections' then
        return {
            job_id = jobId, status = 'ok', photos = Json.array({}),
            data = { collections = Collections.list() },
        }
    elseif jobType == 'create_collection' then
        local p = job.payload or {}
        return {
            job_id = jobId, status = 'ok', photos = Json.array({}),
            data = Collections.create(p.name, p.parent),
        }
    elseif jobType == 'list_develop_presets' then
        return {
            job_id = jobId, status = 'ok', photos = Json.array({}),
            data = { presets = Presets.list() },
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
    local job, status, rawBody = HttpClient.get('/jobs/pending', 5)
    if status == nil then
        return false   -- App non démarrée : on réessaiera
    end
    if status == 204 then
        return true    -- connecté, pas de job
    end
    if job == nil then
        -- 200 avec body indécodable ≠ « pas de job » : le job vient d'être popé
        -- côté App (IN_PROGRESS) et serait perdu jusqu'au TTL 900 s. On ne peut
        -- pas le récupérer ici, mais on LOGGUE (revue Fable 5 L-06).
        if status == 200 then
            Utils.logf('pollOnce : HTTP 200 mais body indécodable (%d octets) — job perdu ? body=%s',
                rawBody and #rawBody or 0, string.sub(tostring(rawBody), 1, 200))
        end
        return true
    end

    Utils.logf('Job reçu : type=%s id=%s', tostring(job.type), tostring(job.job_id))

    -- Appel via global : récupère le dispatch le plus récent après rechargement plugin.
    local currentDispatch = _G.LR_AUTOMATION_DISPATCH or dispatch
    local ok, result = LrTasks.pcall(currentDispatch, job)
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

    -- POST du résultat avec retries (revue Fable 5 L-07) : le job a déjà été
    -- EXÉCUTÉ (apply compris) — perdre le POST fait timeouter le worker App alors
    -- que le travail est fait. status nil = perte réseau → 2 retries avec backoff.
    local postStatus
    for attempt = 1, 3 do
        local _, st = HttpClient.postJsonRaw('/jobs/' .. job.job_id .. '/result', payload, 10)
        postStatus = st
        if postStatus ~= nil then break end
        Utils.logf('POST result : échec réseau (tentative %d/3), retry…', attempt)
        LrTasks.sleep(0.5 * attempt)
    end
    if postStatus == nil then
        Utils.logf('POST result : ABANDON après 3 tentatives — résultat du job %s perdu (travail déjà exécuté)',
            tostring(job.job_id))
    else
        Utils.logf('POST result → HTTP %s', tostring(postStatus))
    end
    return true
end

-- Démarre le pont. TOUJOURS démarre une boucle neuve, identifiée par un jeton de
-- génération unique (_G.LR_AUTOMATION_BRIDGE_GEN). Démarrer incrémente le jeton :
-- toute boucle antérieure (génération plus ancienne) se retire d'elle-même au tour
-- suivant. On a donc au plus UNE boucle vivante, sans flag booléen partagé qu'une
-- boucle mourante pourrait remettre à false pour tuer la boucle active.
--
-- Conséquence : recliquer « connecter » répare toujours le pont (la nouvelle
-- boucle supersède un éventuel zombie au lieu de refuser de démarrer).
function PollingLoop.start()
    -- Retire toute boucle d'une version antérieure du module : elle surveillait
    -- le flag booléen LR_AUTOMATION_BRIDGE_RUNNING (et non la génération).
    _G.LR_AUTOMATION_BRIDGE_RUNNING = false

    local gen = (_G.LR_AUTOMATION_BRIDGE_GEN or 0) + 1
    _G.LR_AUTOMATION_BRIDGE_GEN = gen
    _G.LR_AUTOMATION_BRIDGE_HEARTBEAT = os.time()

    LrFunctionContext.postAsyncTaskWithContext('LrAutomationBridge', function(context)
        -- Le cleanup ne touche AUCUN état partagé : une boucle qui meurt ne peut
        -- pas éteindre une boucle plus récente. Log seul (diagnostic).
        context:addCleanupHandler(function()
            Utils.logf('Pont (gen %d) : contexte nettoyé.', gen)
        end)
        Utils.logf('Pont démarré (gen %d) → %s', gen, HttpClient.BASE_URL)

        -- Tourne tant que cette boucle reste la génération courante.
        while _G.LR_AUTOMATION_BRIDGE_GEN == gen do
            _G.LR_AUTOMATION_BRIDGE_HEARTBEAT = os.time()   -- battement de cœur
            local ok, err = LrTasks.pcall(pollOnce)
            if not ok then
                Utils.logf('Erreur boucle : %s', tostring(err))
            end
            LrTasks.sleep(POLL_INTERVAL)
        end
        Utils.logf('Pont (gen %d) retiré au profit de la gen %s.',
            gen, tostring(_G.LR_AUTOMATION_BRIDGE_GEN))
    end)
    return true
end

-- Arrête le pont : incrémente la génération sans démarrer de boucle → la boucle
-- courante se retire et aucune ne la remplace (le heartbeat devient périmé).
function PollingLoop.stop()
    _G.LR_AUTOMATION_BRIDGE_RUNNING = false
    _G.LR_AUTOMATION_BRIDGE_GEN = (_G.LR_AUTOMATION_BRIDGE_GEN or 0) + 1
end

function PollingLoop.isRunning()
    return bridgeAlive()
end

-- ─── Hot-reload ─────────────────────────────────────────────────────────────
-- Publie le dispatch courant dans un global : la boucle vivante l'appelle via _G
-- (cf. pollOnce), donc recharger le module met à jour le traitement des jobs sans
-- redémarrer la boucle. Le cycle de vie de la boucle est géré par la génération
-- (PollingLoop.start) — plus de bloc de migration fragile ici.
_G.LR_AUTOMATION_DISPATCH = dispatch
-- ────────────────────────────────────────────────────────────────────────────

return PollingLoop
