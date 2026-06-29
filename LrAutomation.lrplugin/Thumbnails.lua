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

return Thumbnails
