--[[
    Thumbnails.lua — récupération de miniatures JPEG via requestJpegThumbnail.

    Écrit chaque miniature dans {projectRoot}/tmp_thumbs/{photo_id}.jpg pour que
    l'App Python puisse les lire directement (même machine, pas d'encodage base64).

    requestJpegThumbnail est async : on attend les callbacks via LrTasks.sleep.
    Timeout THUMB_TIMEOUT secondes si Lr ne génère pas la miniature (preview manquante).
]]

local LrApplication = import 'LrApplication'
local LrFileUtils   = import 'LrFileUtils'
local LrPathUtils   = import 'LrPathUtils'
local LrTasks       = import 'LrTasks'
local Utils         = require 'Utils'

local Thumbnails = {}

local THUMB_TIMEOUT = 15  -- secondes max pour toutes les miniatures d'un lot
-- Délai laissé à Lr pour régénérer l'aperçu après un applyDevelopSettings, avant
-- de demander la miniature sondée (cf. Thumbnails.fetchProbe).
local SETTLE = 0.6

-- Répertoire de sortie : {projectRoot}/tmp_thumbs (créé si absent).
local function thumbsDir()
    local dir = LrPathUtils.child(Utils.projectRoot(), 'tmp_thumbs')
    if not LrFileUtils.exists(dir) then
        LrFileUtils.createDirectory(dir)
    end
    return dir
end

--[[
    Thumbnails.fetch(photos, width, height)

    `photos` : table de LrPhoto (ex. catalog:getTargetPhotos()).
    `width`, `height` : taille max de la miniature (défaut 512×512).

    Retourne un tableau de tables :
        { photo_id, thumbnail_path, error }
    thumbnail_path = chemin absolu du JPEG écrit, ou nil si erreur.
]]
function Thumbnails.fetch(photos, width, height)
    width  = width  or 512
    height = height or 512

    local dir     = thumbsDir()
    local pending = #photos
    local results = {}

    for i, photo in ipairs(photos) do
        local photoId = photo:getRawMetadata('uuid')
        local outPath = LrPathUtils.child(dir, photoId .. '.jpg')
        results[i]    = { photo_id = photoId, thumbnail_path = nil, error = nil }

        -- requestJpegThumbnail est async : callback déclenché quand la miniature est prête.
        photo:requestJpegThumbnail(width, height, function(jpeg, err)
            if jpeg and #jpeg > 0 then
                local f = io.open(outPath, 'wb')
                if f then
                    f:write(jpeg)
                    f:close()
                    results[i].thumbnail_path = outPath
                    Utils.logf('Thumbnail écrit : %s (%d octets)', outPath, #jpeg)
                else
                    results[i].error = 'io.open failed: ' .. outPath
                    Utils.logf('Thumbnail : io.open impossible → %s', outPath)
                end
            else
                results[i].error = tostring(err or 'pas de JPEG retourné')
                Utils.logf('Thumbnail manquant pour %s : %s', photoId, results[i].error)
            end
            pending = pending - 1
        end)
    end

    -- Attente coopérative : LrTasks.sleep cède la main à Lr pour traiter les callbacks.
    local elapsed = 0
    while pending > 0 and elapsed < THUMB_TIMEOUT do
        LrTasks.sleep(0.1)
        elapsed = elapsed + 0.1
    end

    if pending > 0 then
        Utils.logf('Thumbnails.fetch : timeout (%ds), %d en attente', THUMB_TIMEOUT, pending)
        -- Marque comme erreur les entrées toujours en attente.
        for i = 1, #results do
            if results[i].thumbnail_path == nil and results[i].error == nil then
                results[i].error = 'timeout'
            end
        end
    end

    return results
end

--[[
    Thumbnails.fetchProbe(adjustments, width, height)

    Rendu SONDÉ : applique des réglages temporaires, rend la miniature de l'état
    obtenu, puis RESTAURE l'état develop d'origine. Sert au calage de la réponse
    ∂rendu/∂curseur côté App (core.response) et à la boucle fermée d'exposition.

    `adjustments` : liste de { photo_id = uuid, develop = { PascalCase = valeur } }.
    Retourne le même format que Thumbnails.fetch ({ photo_id, thumbnail_path, error }).

    ⚠️ HYPOTHÈSE BLOQUANTE À VÉRIFIER EN VRAI : requestJpegThumbnail doit refléter les
    réglages qu'on vient d'appliquer, pas un aperçu en cache périmé. Si Lr renvoie
    l'ancien rendu, ce chemin est inexploitable et il faut replier sur un export
    (LrExportSession). Le délai SETTLE laisse Lr régénérer l'aperçu avant la demande.

    Mute l'historique develop (apply puis restore) → réservé au calage occasionnel,
    pas à un traitement par photo de masse.
]]
function Thumbnails.fetchProbe(adjustments, width, height)
    width  = width  or 512
    height = height or 512
    local catalog = LrApplication.activeCatalog()

    -- Index uuid → photo sur la sélection courante.
    local byUuid = {}
    for _, photo in ipairs(catalog:getTargetPhotos()) do
        byUuid[photo:getRawMetadata('uuid')] = photo
    end

    -- Capture l'état d'origine + liste les cibles valides.
    local targets, original = {}, {}
    for _, adj in ipairs(adjustments) do
        local photo = byUuid[adj.photo_id]
        if photo and adj.develop then
            original[adj.photo_id] = photo:getDevelopSettings()  -- snapshot complet
            targets[#targets + 1]  = { photo = photo, id = adj.photo_id, develop = adj.develop }
        end
    end

    -- 1. Applique les réglages sondés (transaction).
    catalog:withWriteAccessDo('Lr Automation : sonde (apply)', function()
        for _, t in ipairs(targets) do
            LrTasks.pcall(function() t.photo:applyDevelopSettings(t.develop) end)
        end
    end)

    -- Laisse Lr régénérer l'aperçu avant de demander les miniatures.
    LrTasks.sleep(SETTLE)

    -- 2. Rend les miniatures de l'état sondé.
    local photos = {}
    for _, t in ipairs(targets) do photos[#photos + 1] = t.photo end
    local results = Thumbnails.fetch(photos, width, height)

    -- 3. Restaure l'état d'origine (transaction).
    catalog:withWriteAccessDo('Lr Automation : sonde (restore)', function()
        for _, t in ipairs(targets) do
            local orig = original[t.id]
            if orig then
                LrTasks.pcall(function() t.photo:applyDevelopSettings(orig) end)
            end
        end
    end)

    return results
end

return Thumbnails
