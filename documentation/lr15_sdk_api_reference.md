# Lightroom Classic 15 — Référence API SDK (Lua)
# Camera Raw 18

> SDK version : Lr Classic 15.x — Camera Raw 18.x
> Langage plugin : Lua 5.1
> Toutes les fonctions SDK s'importent avec `import 'LrXxx'`

---

## Table des matières

1. [Imports et structure de base](#1-imports-et-structure-de-base)
2. [LrApplication — Application et catalog](#2-lrapplication)
3. [LrCatalog — Catalog et photos](#3-lrcatalog)
4. [LrPhoto — Données photo](#4-lrphoto)
5. [LrDevelopController — Module Développement](#5-lrdevelopcontroller)
6. [Paramètres Develop — Camera Raw 18](#6-paramètres-develop--camera-raw-18)
7. [LrTasks — Asynchronisme](#7-lrtasks)
8. [LrHttp — Requêtes HTTP](#8-lrhttp)
9. [LrSocket — Sockets TCP](#9-lrsocket)
10. [LrDialogs — Boîtes de dialogue](#10-lrdialogs)
11. [LrProgressScope — Barre de progression](#11-lrprogressscope)
12. [LrFileUtils / LrPathUtils — Fichiers et chemins](#12-lrfileutils--lrpathutils)
13. [LrLogger — Logs](#13-lrlogger)
14. [LrShell — Lancer des processus externes](#14-lrshell)
15. [LrMobdebug — Débogage](#15-lrmobdebug)
16. [Info.lua — Manifeste plugin](#16-infolua--manifeste-plugin)
17. [Patterns courants](#17-patterns-courants)
18. [Limitations et fonctionnalités non accessibles via SDK](#18-limitations)

---

## 1. Imports et structure de base

```lua
-- Syntaxe d'import SDK (pas de require standard)
local LrApplication       = import 'LrApplication'
local LrCatalog           = import 'LrCatalog'
local LrPhoto             = import 'LrPhoto'         -- rarement importé directement
local LrDevelopController = import 'LrDevelopController'
local LrTasks             = import 'LrTasks'
local LrHttp              = import 'LrHttp'
local LrSocket            = import 'LrSocket'
local LrDialogs           = import 'LrDialogs'
local LrProgressScope     = import 'LrProgressScope'
local LrFileUtils         = import 'LrFileUtils'
local LrPathUtils         = import 'LrPathUtils'
local LrLogger            = import 'LrLogger'
local LrShell             = import 'LrShell'
local LrView              = import 'LrView'
local LrBinding           = import 'LrBinding'
local LrFunctionContext   = import 'LrFunctionContext'
local LrColor             = import 'LrColor'
local LrDate              = import 'LrDate'
local LrStringUtils       = import 'LrStringUtils'
local LrSystemInfo        = import 'LrSystemInfo'
local LrLocalization      = import 'LrLocalization'
```

---

## 2. LrApplication

```lua
local LrApplication = import 'LrApplication'

-- Accès au catalog actif
local catalog = LrApplication.activeCatalog()

-- Version Lightroom
local version = LrApplication.versionTable()
-- version.major, version.minor, version.revision, version.build
-- Ex : { major=15, minor=0, revision=0, build=... }

-- Version Camera Raw
local crVersion = LrApplication.cameraRawVersion()
-- Retourne string ex. "18.0.0"

-- Chemin d'installation Lr
local appPath = LrApplication.applicationDirectory()

-- Dossier préférences utilisateur
local prefsPath = LrApplication.userDataPath()

-- Plateforme
local platform = LrApplication.platform()  -- "Windows" ou "Macintosh"

-- Ouvrir URL dans le navigateur
LrApplication.openWebsite('http://localhost:5000')

-- Quitter Lightroom (utiliser avec précaution)
LrApplication.quit()

-- Accès aux préférences plugin (persistantes entre sessions)
local prefs = import 'LrPrefs'
local pluginPrefs = prefs.prefsForPlugin(_PLUGIN)
pluginPrefs.myKey = 'myValue'
local val = pluginPrefs.myKey
```

---

## 3. LrCatalog

```lua
local catalog = LrApplication.activeCatalog()

-- ─── Sélection ────────────────────────────────────────────────────────────────

-- Photos sélectionnées (actives dans la grille ou dans le module Développement)
local photos = catalog:getTargetPhotos()

-- Photo active unique (module Développement)
local activePhoto = catalog:getTargetPhoto()

-- Collection active
local activeCollection = catalog:getActiveCollection()

-- Changer la sélection
catalog:setSelectedPhotos(targetPhoto, { photo1, photo2, photo3 })

-- ─── Transactions d'écriture ──────────────────────────────────────────────────

-- OBLIGATOIRE pour toute modification catalog / develop settings
catalog:withWriteAccessDo('Nom de l\'action undo', function(context)
    -- Modifications ici
    photo:applyDevelopSettings({ Exposure = 0.5 })
    photo:setRawMetadata('rating', 5)
end)

-- Version asynchrone (ne bloque pas l'UI)
catalog:withWriteAccessDo('Action', function(context)
    -- ...
end, { timeout = 10 })  -- timeout en secondes

-- ─── Recherche ────────────────────────────────────────────────────────────────

-- Toutes les photos du catalog
local allPhotos = catalog:getAllPhotos()

-- Recherche par critères
local results = catalog:findPhotos({
    searchDesc = {
        criteria = 'rating',
        operation = '>=',
        value = 4,
    }
})

-- ─── Collections ──────────────────────────────────────────────────────────────

local rootSet   = catalog:getRootCollectionSet()
local allColls  = catalog:getChildCollections()

-- Créer une collection
catalog:withWriteAccessDo('Create collection', function()
    catalog:createCollection('Nom collection', nil, true)
end)

-- ─── Événements catalog ───────────────────────────────────────────────────────

-- Observer les changements de sélection
catalog:addListener(function(event)
    if event == 'catalogChangeBegin' then end
    if event == 'catalogChanged' then end
end)
```

---

## 4. LrPhoto

Les méthodes sont appelées sur l'objet photo retourné par `catalog:getTargetPhotos()`.

### Métadonnées en lecture (`getRawMetadata`)

```lua
-- Chemins
local rawPath     = photo:getRawMetadata('path')           -- Chemin absolu fichier RAW/original
local proxyPath   = photo:getRawMetadata('proxyImagePath') -- Chemin JPEG proxy (si disponible)

-- Identifiant unique Lr interne
local photoId     = photo:getRawMetadata('uuid')

-- EXIF
local isoSpeed    = photo:getRawMetadata('isoSpeedRating')      -- number
local aperture    = photo:getRawMetadata('aperture')            -- f-number (ex. 2.8)
local shutter     = photo:getRawMetadata('shutterSpeed')        -- fraction (ex. 0.005 = 1/200s)
local focal       = photo:getRawMetadata('focalLength')         -- mm
local focalIn35   = photo:getRawMetadata('focalLength35mm')     -- mm équiv. plein format
local camera      = photo:getRawMetadata('cameraModel')         -- ex. "ILCE-7M4"
local lens        = photo:getRawMetadata('lensModel')
local make        = photo:getRawMetadata('cameraMake')          -- ex. "SONY"
local captureDate = photo:getRawMetadata('dateTimeOriginal')    -- LrDate
local flashFired  = photo:getRawMetadata('flashFired')          -- boolean
local orientation = photo:getRawMetadata('orientation')         -- 'AB', 'BC', etc.

-- Dimensions
local width       = photo:getRawMetadata('width')               -- pixels (original)
local height      = photo:getRawMetadata('height')
local croppedW    = photo:getRawMetadata('croppedWidth')        -- après crop Lr
local croppedH    = photo:getRawMetadata('croppedHeight')

-- GPS
local gps         = photo:getRawMetadata('gps')
-- gps.latitude, gps.longitude (ou nil si absent)

-- Notation et classement
local rating      = photo:getRawMetadata('rating')              -- 0-5 (nil = non noté)
local colorLabel  = photo:getRawMetadata('colorNameForLabel')   -- 'red','yellow','green','blue','purple',''
local pickStatus  = photo:getRawMetadata('pickStatus')          -- 1=retenu, 0=neutre, -1=rejeté

-- Type fichier
local isVirtual   = photo:getRawMetadata('isVirtualCopy')       -- boolean
local isVideo     = photo:getRawMetadata('isVideo')             -- boolean
local fileFormat  = photo:getRawMetadata('fileFormat')          -- 'RAW','JPEG','TIFF','DNG','PSD'

-- Collection
local collections = photo:getRawMetadata('collections')        -- table de collections

-- Taille fichier
local fileSize    = photo:getRawMetadata('fileSize')            -- bytes
```

### Métadonnées IPTC en lecture/écriture (`getFormattedMetadata` / `setRawMetadata`)

```lua
-- Lecture
local title    = photo:getFormattedMetadata('title')
local caption  = photo:getFormattedMetadata('caption')
local keywords = photo:getFormattedMetadata('keywordTagsForExport')
local creator  = photo:getFormattedMetadata('creator')
local location = photo:getFormattedMetadata('location')
local city     = photo:getFormattedMetadata('city')
local country  = photo:getFormattedMetadata('country')

-- Écriture (dans withWriteAccessDo)
photo:setRawMetadata('title', 'Mon titre')
photo:setRawMetadata('caption', 'Ma légende')
photo:setRawMetadata('rating', 5)           -- 0-5
photo:setRawMetadata('colorLabel', 'red')   -- 'red','yellow','green','blue','purple',''
photo:setRawMetadata('pickStatus', 1)       -- 1, 0, -1
photo:setRawMetadata('gps', { latitude = 48.8566, longitude = 2.3522 })
```

### Develop settings

```lua
-- Lire les paramètres develop actuels
local devSettings = photo:getDevelopSettings()
-- devSettings.Exposure, devSettings.Temperature, devSettings.Tint, etc.

-- Appliquer des paramètres develop (dans withWriteAccessDo)
photo:applyDevelopSettings({
    Exposure    = 0.35,
    Temperature = 5600,
    Tint        = -5,
})

-- Appliquer un preset develop
local presets = LrApplication.developPresets()
for _, preset in ipairs(presets) do
    if preset:getName() == 'Mon Preset' then
        photo:applyDevelopPreset(preset, _PLUGIN)
    end
end

-- Réinitialiser aux valeurs par défaut Camera Raw
photo:applyDevelopSettings({}, 'resetToDefault')

-- Copier les develop settings d'une photo vers une autre
local srcSettings = srcPhoto:getDevelopSettings()
catalog:withWriteAccessDo('Paste settings', function()
    dstPhoto:applyDevelopSettings(srcSettings)
end)

-- Historique develop
local history = photo:getDevelopHistory()
-- history est une table d'entrées { name, id }
photo:setDevelopHistoryEntry(history[1])  -- revenir à une étape
```

---

## 5. LrDevelopController

> Opère uniquement sur la photo active dans le **module Développement**.
> Pour batch, préférer `photo:applyDevelopSettings()`.

```lua
local LrDevelopController = import 'LrDevelopController'

-- Lire une valeur
local expVal = LrDevelopController.getValue('Exposure')

-- Définir une valeur
LrDevelopController.setValue('Exposure', 0.5)
LrDevelopController.setValue('Temperature', 5600)

-- Incrémenter / décrémenter
LrDevelopController.increment('Exposure')  -- +1 pas
LrDevelopController.decrement('Exposure')

-- Revenir aux valeurs par défaut d'un paramètre
LrDevelopController.resetToDefault('Exposure')

-- Sélectionner un outil
LrDevelopController.selectTool('crop')      -- 'crop','spot','redeye','gradient','brush','none'

-- Activer / désactiver le before/after
LrDevelopController.setMultipleSelectionMode(true)

-- Réinitialiser tout le développement
LrDevelopController.resetAllDevelopAdjustments()

-- Accès aux masques (Lr 11+)
-- Note : la manipulation des masques via SDK est limitée
-- Les masques IA (Subject, Sky, etc.) ne sont pas créables via SDK directement
```

---

## 6. Paramètres Develop — Camera Raw 18

### Exposition et tonalité

| Paramètre | Plage | Description |
|---|---|---|
| `Exposure` | -5.0 à +5.0 | Exposition globale (stops) |
| `Contrast` | -100 à +100 | Contraste |
| `Highlights` | -100 à +100 | Récupération hautes lumières |
| `Shadows` | -100 à +100 | Débouchage ombres |
| `Whites` | -100 à +100 | Point blanc |
| `Blacks` | -100 à +100 | Point noir |
| `Clarity` | -100 à +100 | Clarté (contraste local midtones) |
| `Dehaze` | -100 à +100 | Réduction/ajout de voile atmosphérique |

### Balance des blancs

| Paramètre | Plage | Description |
|---|---|---|
| `Temperature` | 2000 à 50000 | Température couleur (Kelvin) |
| `Tint` | -150 à +150 | Teinte (vert ↔ magenta) |
| `WhiteBalance` | string | `'AsShot'`, `'Auto'`, `'Custom'`, `'Daylight'`, `'Cloudy'`, `'Shade'`, `'Tungsten'`, `'Fluorescent'`, `'Flash'` |

### Vibrance et saturation

| Paramètre | Plage | Description |
|---|---|---|
| `Vibrance` | -100 à +100 | Vibrance (saturation intelligente) |
| `Saturation` | -100 à +100 | Saturation globale |

### HSL / Couleur (8 canaux × 3 propriétés)

Canaux : `Red`, `Orange`, `Yellow`, `Green`, `Aqua`, `Blue`, `Purple`, `Magenta`

| Modèle | Clé SDK | Plage |
|---|---|---|
| Teinte | `HueAdjustmentRed` … `HueAdjustmentMagenta` | -100 à +100 |
| Saturation | `SaturationAdjustmentRed` … `SaturationAdjustmentMagenta` | -100 à +100 |
| Luminance | `LuminanceAdjustmentRed` … `LuminanceAdjustmentMagenta` | -100 à +100 |

### Color Grading (Lr 10+ / Camera Raw 13+)

| Paramètre | Plage | Description |
|---|---|---|
| `ColorGradeShadowHue` | 0 à 360 | Teinte ombres |
| `ColorGradeShadowSat` | 0 à 100 | Saturation ombres |
| `ColorGradeShadowLum` | -100 à +100 | Luminosité ombres |
| `ColorGradeMidtoneHue` | 0 à 360 | Teinte tons moyens |
| `ColorGradeMidtoneSat` | 0 à 100 | Saturation tons moyens |
| `ColorGradeMidtoneLum` | -100 à +100 | Luminosité tons moyens |
| `ColorGradeHighlightHue` | 0 à 360 | Teinte hautes lumières |
| `ColorGradeHighlightSat` | 0 à 100 | Saturation hautes lumières |
| `ColorGradeHighlightLum` | -100 à +100 | Luminosité hautes lumières |
| `ColorGradeGlobalHue` | 0 à 360 | Teinte globale |
| `ColorGradeGlobalSat` | 0 à 100 | Saturation globale |
| `ColorGradeGlobalLum` | -100 à +100 | Luminosité globale |
| `ColorGradeBlending` | 0 à 100 | Fusion entre zones |
| `ColorGradeBalance` | -100 à +100 | Balance ombres/hautes lumières |

### Point Color (Lr 13+ / Camera Raw 16+)

> Le Point Color est partiellement exposé via SDK dans Lr 13+.
> Manipulation complète via UI uniquement dans certains cas.

| Paramètre | Type | Description |
|---|---|---|
| `PointColorHueName` | string | Nom de la couleur ciblée (interne) |
| `PointColorHue` | 0 à 360 | Teinte du point couleur |
| `PointColorSaturation` | 0 à 100 | Saturation ciblée |
| `PointColorLuminance` | 0 à 100 | Luminance ciblée |

### Courbe des tons (Tone Curve)

| Paramètre | Plage | Description |
|---|---|---|
| `ToneCurve` | table de points | Courbe globale `{ { input, output }, … }` |
| `ToneCurveRed` | table de points | Courbe canal Rouge |
| `ToneCurveGreen` | table de points | Courbe canal Vert |
| `ToneCurveBlue` | table de points | Courbe canal Bleu |
| `ParametricShadows` | -100 à +100 | Ombres (curseur paramétrique) |
| `ParametricDarks` | -100 à +100 | Sombres |
| `ParametricLights` | -100 à +100 | Clairs |
| `ParametricHighlights` | -100 à +100 | Hautes lumières |
| `ParametricShadowSplit` | 10 à 70 | Position split ombres/sombres |
| `ParametricMidtoneSplit` | 20 à 80 | Position split sombres/clairs |
| `ParametricHighlightSplit` | 30 à 90 | Position split clairs/HL |

### Netteté et clarté

| Paramètre | Plage | Description |
|---|---|---|
| `Sharpness` | 0 à 150 | Netteté (rayon × détail × masque) |
| `SharpenRadius` | 0.5 à 3.0 | Rayon netteté |
| `SharpenDetail` | 0 à 100 | Détail netteté |
| `SharpenEdgeMasking` | 0 à 100 | Masque de bord |
| `Clarity` | -100 à +100 | Clarté |
| `Texture` | -100 à +100 | Texture (détail fin) |

### Réduction du bruit

| Paramètre | Plage | Description |
|---|---|---|
| `LuminanceSmoothing` | 0 à 100 | Bruit luminance (classique) |
| `LuminanceNoiseReductionDetail` | 0 à 100 | Détail bruit luminance |
| `LuminanceNoiseReductionContrast` | 0 à 100 | Contraste bruit luminance |
| `ColorNoiseReduction` | 0 à 100 | Bruit couleur |
| `ColorNoiseReductionDetail` | 0 à 100 | Détail bruit couleur |
| `ColorNoiseReductionSmoothness` | 0 à 100 | Lissage bruit couleur |

### Denoise AI (Lr 12.3+ / Camera Raw 15.3+)

> Denoise AI lance un processus lourd côté Lightroom.
> Accessible via `photo:applyDevelopSettings()` mais l'application effective
> nécessite que Lr traite la photo (peut être asynchrone côté Lr).

| Paramètre | Plage | Description |
|---|---|---|
| `AINoiseReduction` | boolean / 0-1 | Activer Denoise AI |
| `AINoiseReductionAmount` | 0 à 100 | Force de la réduction IA |

### Lens Corrections

| Paramètre | Valeur | Description |
|---|---|---|
| `LensProfileEnable` | 0 / 1 | Activer correction profil objectif |
| `LensManualDistortionAmount` | -100 à +100 | Correction distorsion manuelle |
| `VignetteAmount` | -100 à +100 | Correction vignettage |
| `ChromaticAberrationR` | -100 à +100 | Aberration chromatique Rouge |
| `ChromaticAberrationB` | -100 à +100 | Aberration chromatique Bleu |
| `EnableLensCorrections` | boolean | Corrections objectif activées |
| `AutoLateralCA` | 0 / 1 | Correction auto aberration chromatique latérale |

### Transformation géométrique

| Paramètre | Plage | Description |
|---|---|---|
| `PerspectiveVertical` | -100 à +100 | Correction verticale |
| `PerspectiveHorizontal` | -100 à +100 | Correction horizontale |
| `PerspectiveRotate` | -10 à +10 | Rotation |
| `PerspectiveScale` | 50 à 150 | Échelle |
| `PerspectiveAspect` | -100 à +100 | Ratio |
| `UprightMode` | string | `'Off'`, `'Auto'`, `'Level'`, `'Vertical'`, `'Full'`, `'Guided'` |

### Effets

| Paramètre | Plage | Description |
|---|---|---|
| `GrainAmount` | 0 à 100 | Grain |
| `GrainSize` | 25 à 100 | Taille grain |
| `GrainFrequency` | 0 à 100 | Fréquence grain |
| `PostCropVignetteAmount` | -100 à +100 | Vignettage post-recadrage |
| `PostCropVignetteMidpoint` | 0 à 100 | Point médian vignettage |
| `PostCropVignetteFeather` | 0 à 100 | Contour vignettage |
| `PostCropVignetteRoundness` | -100 à +100 | Rondeur vignettage |
| `PostCropVignetteStyle` | 1 / 2 / 3 | 1=Couleur, 2=Recouvrement, 3=Peinture |

### Recadrage

| Paramètre | Plage | Description |
|---|---|---|
| `CropTop` | 0.0 à 1.0 | Bord haut (proportion) |
| `CropLeft` | 0.0 à 1.0 | Bord gauche |
| `CropBottom` | 0.0 à 1.0 | Bord bas |
| `CropRight` | 0.0 à 1.0 | Bord droit |
| `CropAngle` | -45 à +45 | Angle recadrage |
| `CropConstrainToWarp` | 0 / 1 | Contraindre au déformation |

### Calibration caméra

| Paramètre | Plage | Description |
|---|---|---|
| `CameraProfile` | string | Nom du profil caméra (ex. `'Camera Standard'`, `'Camera Neutral'`, `'Adobe Standard'`) |
| `ShadowTint` | -100 à +100 | Teinte ombres calibration |
| `RedHue` | -100 à +100 | Teinte Rouge calibration |
| `RedSaturation` | -100 à +100 | Saturation Rouge calibration |
| `GreenHue` | -100 à +100 | Teinte Verte calibration |
| `GreenSaturation` | -100 à +100 | Saturation Verte calibration |
| `BlueHue` | -100 à +100 | Teinte Bleue calibration |
| `BlueSaturation` | -100 à +100 | Saturation Bleue calibration |

### Version de process

| Paramètre | Valeur | Description |
|---|---|---|
| `ProcessVersion` | string | `'15.0'` pour Camera Raw 15+ / Lr 12+ |

> Ne jamais modifier `ProcessVersion` sauf si migration délibérée.
> Camera Raw 18 utilise ProcessVersion `'15.0'` pour les paramètres récents.

---

## 7. LrTasks

```lua
local LrTasks = import 'LrTasks'

-- Lancer une tâche asynchrone (ne bloque pas l'UI Lr)
LrTasks.startAsyncTask(function()
    -- Code asynchrone ici
    -- Tout I/O bloquant, HTTP, sleep doivent être ici
    LrTasks.sleep(0.3)  -- secondes (float)
end)

-- Attendre dans une tâche async
LrTasks.sleep(1.0)  -- 1 seconde

-- Yield (céder le contrôle brièvement — bonne pratique dans les longues boucles)
LrTasks.yield()

-- Vérifier si annulation demandée (utiliser avec LrProgressScope)
-- Voir section LrProgressScope

-- Créer une tâche nommée (pour debug)
LrTasks.startAsyncTask(function()
    -- ...
end)

-- Boucle polling typique pour ce projet
LrTasks.startAsyncTask(function()
    local LrHttp = import 'LrHttp'
    while true do
        local ok, result = pcall(function()
            local body, headers = LrHttp.get('http://localhost:5000/jobs/pending', {})
            return body
        end)
        if ok and result and result ~= '' then
            -- traiter le job
        end
        LrTasks.sleep(0.3)
    end
end)
```

---

## 8. LrHttp

```lua
local LrHttp = import 'LrHttp'

-- ─── GET ───────────────────────────────────────────────────────────────────────

local body, headers = LrHttp.get(
    'http://localhost:5000/health',
    {}   -- headers additionnels (table vide = aucun)
)
-- body   : string (corps réponse)
-- headers : table { status = '200 OK', ... }

-- ─── POST ──────────────────────────────────────────────────────────────────────

local jsonBody = '{"key":"value"}'
local body, headers = LrHttp.post(
    'http://localhost:5000/jobs/abc/result',
    jsonBody,
    {
        { field = 'Content-Type', value = 'application/json' },
        { field = 'Accept',       value = 'application/json' },
    }
)

-- ─── POST multipart (upload fichier) ──────────────────────────────────────────

local body, headers = LrHttp.postMultipart(
    'http://localhost:5000/upload',
    {
        { name = 'file', filePath = '/path/to/file.jpg', fileName = 'image.jpg' },
        { name = 'field', value = 'data' },
    }
)

-- ─── Gestion des erreurs ───────────────────────────────────────────────────────

local body, headers = LrHttp.get(url, {})

if headers == nil then
    -- Erreur réseau / App non démarrée
    LrDialogs.message('Erreur', 'App non accessible sur localhost:5000')
    return
end

local statusCode = headers.status  -- ex. "200 OK", "404 Not Found"
local code = tonumber(statusCode:match('^(%d+)'))

if code ~= 200 then
    -- Erreur HTTP
end

-- ─── Timeout ───────────────────────────────────────────────────────────────────

-- LrHttp n'expose pas de paramètre timeout explicite dans Lr 15
-- Utiliser pcall pour intercepter les erreurs réseau
local ok, result = pcall(function()
    return LrHttp.get('http://localhost:5000/health', {})
end)
if not ok then
    -- Timeout ou erreur réseau
end
```

---

## 9. LrSocket

> Pour ce projet : LrSocket non nécessaire (LrHttp client suffit).
> Documenter pour référence.

```lua
local LrSocket = import 'LrSocket'

-- Créer un socket client TCP
local socket = LrSocket.bind {
    functionContext = context,
    plugin          = _PLUGIN,
    port            = 0,         -- port local (0 = auto)
    mode            = 'send',    -- 'send' ou 'receive'
    callback        = function(socket, message)
        -- reçu message
    end,
}

-- Envoyer
socket:send('message')

-- Fermer
socket:close()
```

---

## 10. LrDialogs

```lua
local LrDialogs = import 'LrDialogs'

-- Message simple
LrDialogs.message('Titre', 'Message', 'info')    -- 'info', 'warning', 'critical'

-- Confirmation oui/non
local result = LrDialogs.confirm('Titre', 'Appliquer les ajustements ?', 'Appliquer', 'Annuler')
-- result : 'ok' ou 'cancel'

-- Saisie texte
local value = LrDialogs.runOpenPanel({
    title                = 'Sélectionner un dossier',
    canChooseFiles       = false,
    canChooseDirectories = true,
    allowsMultipleSelection = false,
})

-- Sélection fichier
local paths = LrDialogs.runOpenPanel({
    title                = 'Sélectionner des fichiers RAW',
    canChooseFiles       = true,
    canChooseDirectories = false,
    allowsMultipleSelection = true,
    fileTypes            = { 'ARW', 'raw', 'dng' },
})

-- Sauvegarde fichier
local savePath = LrDialogs.runSavePanel({
    title    = 'Enregistrer',
    fileName = 'export.json',
})

-- Dialogue personnalisé (LrView)
local LrView = import 'LrView'
local f = LrView.osFactory()

local result = LrDialogs.presentModalDialog({
    title   = 'Paramètres',
    contents = f:column {
        f:row {
            f:static_text { title = 'Port :' },
            f:edit_field {
                value = LrView.bind 'port',
                width_in_chars = 6,
            },
        },
    },
    actionVerb = 'OK',
})
-- result : 'ok' ou 'cancel'
```

---

## 11. LrProgressScope

```lua
local LrProgressScope = import 'LrProgressScope'
local LrFunctionContext = import 'LrFunctionContext'

LrFunctionContext.callWithContext('Progress', function(context)
    local progress = LrProgressScope({
        title         = 'Analyse des photos',
        functionContext = context,
        caption       = 'Initialisation...',
    })

    local photos = catalog:getTargetPhotos()
    local total  = #photos

    for i, photo in ipairs(photos) do
        if progress:isCanceled() then break end

        progress:setCaption('Traitement : ' .. photo:getRawMetadata('path'))
        progress:setPortionComplete(i - 1, total)

        -- Traitement...
        LrTasks.yield()
    end

    progress:done()
end)
```

---

## 12. LrFileUtils / LrPathUtils

```lua
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'

-- ─── LrPathUtils ───────────────────────────────────────────────────────────────

-- Construire un chemin (gère les séparateurs Windows/Mac)
local fullPath = LrPathUtils.child('C:/photos', 'DSC001.ARW')
-- → 'C:/photos/DSC001.ARW'

-- Extension
local ext = LrPathUtils.extension('DSC001.ARW')     -- 'ARW'

-- Nom de fichier sans extension
local stem = LrPathUtils.removeExtension('DSC001.ARW')  -- 'DSC001'

-- Dossier parent
local dir = LrPathUtils.parent('C:/photos/DSC001.ARW')  -- 'C:/photos'

-- Dossier temporaire
local tempDir = LrPathUtils.getStandardFilePath('temp')

-- Dossier documents utilisateur
local docsDir = LrPathUtils.getStandardFilePath('documents')

-- ─── LrFileUtils ───────────────────────────────────────────────────────────────

-- Lire un fichier texte
local content = LrFileUtils.readFile('C:/chemin/fichier.json')

-- Écrire un fichier texte
LrFileUtils.writeFile('C:/chemin/out.json', '{"key":"val"}')

-- Vérifier existence
local exists = LrFileUtils.exists('C:/chemin/fichier.json')  -- true/false

-- Supprimer
LrFileUtils.delete('C:/chemin/fichier.json')

-- Copier
LrFileUtils.copy('C:/src.json', 'C:/dst.json')

-- Lister les fichiers d'un dossier
local files = LrFileUtils.directoryEntries('C:/photos')
for _, file in ipairs(files) do
    -- file = chemin complet de chaque entrée
end

-- Créer dossier
LrFileUtils.createAllDirectories('C:/photos/nouveau/dossier')
```

---

## 13. LrLogger

```lua
local LrLogger = import 'LrLogger'

-- Créer un logger nommé
local logger = LrLogger('LrAutomation')

-- Activer la sortie (console Lr : Aide > Console Lua)
logger:enable('logfile')  -- 'logfile', 'print', ou 'all'

-- Niveaux de log
logger:trace('Message trace')
logger:debug('Message debug')
logger:info('Message info')
logger:warn('Message warn')
logger:error('Message erreur')

-- Formatage
logger:info(string.format('Photo : %s — Exposition : %.2f', path, exposure))

-- Log global rapide (visible dans Console Lr sans logger explicite)
print('Debug rapide')  -- visible dans Console Lua de Lr
```

---

## 14. LrShell

```lua
local LrShell = import 'LrShell'

-- Ouvrir un fichier avec l'application par défaut
LrShell.openPathInFileBrowser('C:/photos/DSC001.ARW')

-- Ouvrir un dossier dans l'explorateur
LrShell.revealInFileBrowser('C:/photos')

-- Lancer une commande externe (Windows)
-- Note : LrShell ne capture pas stdout
-- Pour capture stdout, utiliser io.popen (Lua standard)
LrShell.openURL('http://localhost:5000')
```

### Lancer un processus et capturer stdout

```lua
-- io.popen est disponible en Lua 5.1 dans Lr
local handle = io.popen('python "C:/app/analyzer.py" --input "C:/photo.ARW"')
local output = handle:read('*all')
handle:close()

-- Windows : nécessite parfois cmd /c pour les chemins avec espaces
local handle = io.popen('cmd /c python "C:/chemin avec espaces/script.py"')
```

---

## 15. LrMobdebug

```lua
-- Débogage distant avec ZeroBrane Studio
-- Activer dans ZeroBrane, puis dans le plugin :
package.path = package.path .. ';C:/ZeroBraneStudio/lualibs/?.lua'
local mobdebug = require 'mobdebug'
mobdebug.start()  -- connexion au débogueur
```

---

## 16. Info.lua — Manifeste plugin

```lua
-- plugin/Info.lua
-- Fichier obligatoire à la racine du dossier .lrplugin

return {
    LrSdkVersion              = 12.0,    -- version SDK minimum utilisée
    LrSdkMinimumVersion       = 6.0,     -- version SDK minimum supportée

    LrToolkitIdentifier       = 'com.votredomaine.lr-automation',
    LrPluginName              = LOC '$$$/LrAutomation/PluginName=Lr Automation',
    LrPluginInfoUrl           = 'http://localhost:5000',

    LrInitPlugin              = 'Init.lua',         -- optionnel : fichier init au démarrage
    LrShutdownPlugin          = 'Shutdown.lua',     -- optionnel : nettoyage à la fermeture

    LrPluginInfoProvider      = 'InfoProvider.lua', -- optionnel : panneau préférences plugin

    LrLibraryMenuItems = {
        {
            title  = LOC '$$$/LrAutomation/Menu/Analyze=Analyser la sélection',
            file   = 'Menu.lua',
            enabledWhen = 'photosSelected',
        },
        {
            title  = LOC '$$$/LrAutomation/Menu/Start=Démarrer l\'application',
            file   = 'StartApp.lua',
        },
    },

    LrHelpMenuItems = {
        {
            title = 'À propos de Lr Automation',
            file  = 'About.lua',
        },
    },

    VERSION = { major = 1, minor = 0, revision = 0, build = 1 },
}
```

---

## 17. Patterns courants

### Pattern : extraire données complètes d'une sélection

```lua
local function getSelectedPhotosData()
    local catalog = LrApplication.activeCatalog()
    local photos  = catalog:getTargetPhotos()
    local data    = {}

    for _, photo in ipairs(photos) do
        local devSettings = photo:getDevelopSettings()
        table.insert(data, {
            photo_id = photo:getRawMetadata('uuid'),
            path     = photo:getRawMetadata('path'),
            exif = {
                iso          = photo:getRawMetadata('isoSpeedRating'),
                aperture     = photo:getRawMetadata('aperture'),
                shutter      = photo:getRawMetadata('shutterSpeed'),
                focal        = photo:getRawMetadata('focalLength'),
                camera       = photo:getRawMetadata('cameraModel'),
                capture_date = LrDate.timeToIsoDate(
                    photo:getRawMetadata('dateTimeOriginal') or 0
                ),
            },
            current_develop = {
                Exposure    = devSettings.Exposure    or 0,
                Temperature = devSettings.Temperature or 5500,
                Tint        = devSettings.Tint        or 0,
                Highlights  = devSettings.Highlights  or 0,
                Shadows     = devSettings.Shadows     or 0,
                Whites      = devSettings.Whites      or 0,
                Blacks      = devSettings.Blacks      or 0,
                Vibrance    = devSettings.Vibrance    or 0,
                Saturation  = devSettings.Saturation  or 0,
            },
        })
    end

    return data
end
```

### Pattern : appliquer ajustements batch

```lua
local function applyAdjustmentsBatch(adjustments)
    -- adjustments : { { photo_id, develop: { Exposure, Temperature, … } }, … }

    local catalog = LrApplication.activeCatalog()
    local photos  = catalog:getAllPhotos()

    -- Indexer les photos par uuid
    local photoIndex = {}
    for _, photo in ipairs(photos) do
        photoIndex[photo:getRawMetadata('uuid')] = photo
    end

    catalog:withWriteAccessDo('Lr Automation — Apply Adjustments', function()
        for _, adj in ipairs(adjustments) do
            local photo = photoIndex[adj.photo_id]
            if photo then
                photo:applyDevelopSettings(adj.develop)
            end
        end
    end)
end
```

### Pattern : boucle polling avec healthcheck au démarrage

```lua
local function startPollingLoop()
    local LrHttp  = import 'LrHttp'
    local LrTasks = import 'LrTasks'

    LrTasks.startAsyncTask(function()
        -- Attendre que l'App soit disponible
        local appReady = false
        for _ = 1, 20 do  -- 20 tentatives × 500ms = 10 secondes max
            local ok, body = pcall(LrHttp.get, 'http://localhost:5000/health', {})
            if ok and body then
                appReady = true
                break
            end
            LrTasks.sleep(0.5)
        end

        if not appReady then
            LrDialogs.message('Lr Automation',
                'Application non accessible. Lancer app/main.py', 'warning')
            return
        end

        -- Boucle principale
        while true do
            local ok, body = pcall(LrHttp.get, 'http://localhost:5000/jobs/pending', {})
            if ok and body and body ~= '' and body ~= 'null' then
                local job = parseJson(body)  -- utiliser dkjson
                if job and job.type then
                    handleJob(job)
                end
            end
            LrTasks.sleep(0.3)
        end
    end)
end
```

### Pattern : dispatch de jobs

```lua
local function handleJob(job)
    local catalog = LrApplication.activeCatalog()
    local LrHttp  = import 'LrHttp'

    if job.type == 'get_selected_photos' then
        local data = getSelectedPhotosData()
        local json = encodeJson({ job_id = job.job_id, status = 'ok', photos = data })
        LrHttp.post(
            'http://localhost:5000/jobs/' .. job.job_id .. '/result',
            json,
            {{ field = 'Content-Type', value = 'application/json' }}
        )

    elseif job.type == 'apply_adjustments' then
        applyAdjustmentsBatch(job.adjustments)
        local json = encodeJson({ job_id = job.job_id, status = 'ok' })
        LrHttp.post(
            'http://localhost:5000/jobs/' .. job.job_id .. '/result',
            json,
            {{ field = 'Content-Type', value = 'application/json' }}
        )

    else
        -- Job inconnu : retourner erreur
        local json = encodeJson({
            job_id = job.job_id,
            status = 'error',
            message = 'Unknown job type: ' .. tostring(job.type)
        })
        LrHttp.post(
            'http://localhost:5000/jobs/' .. job.job_id .. '/result',
            json,
            {{ field = 'Content-Type', value = 'application/json' }}
        )
    end
end
```

---

## 18. Limitations

### Fonctionnalités NON accessibles via SDK (Lr 15 / Camera Raw 18)

| Fonctionnalité | Statut SDK | Alternative |
|---|---|---|
| Créer des masques IA (Subject, Sky, Background, People) | Non accessible | UI uniquement |
| Modifier des masques existants | Non accessible | UI uniquement |
| Lire les masques (position, type) | Non accessible | — |
| Generative Remove (suppression IA) | Non accessible | UI uniquement |
| Lens Blur AI (flou objectif IA) | Non accessible | UI uniquement |
| Denoise AI (appliquer) | Accessible via `applyDevelopSettings` | Requiert traitement Lr |
| Enhance (Super Resolution, Denoise) | Non accessible via `applyDevelopSettings` | UI ou `LrPhoto:requestJpegThumbnail` |
| Merge HDR / Panorama | Non accessible | UI uniquement |
| Sync des paramètres entre photos | `photo:applyDevelopSettings` équivalent | |
| Accéder au histogramme Lr | Non accessible | Calculer depuis RAW côté Python |
| Lire le résultat visuel du développement | Non accessible | Utiliser `photo:requestJpegThumbnail` |

### `requestJpegThumbnail` — seule façon d'obtenir un rendu

```lua
-- Obtenir un rendu JPEG (aperçu du développement actuel)
photo:requestJpegThumbnail(
    512, 512,    -- largeur, hauteur max
    function(jpegData, reason)
        if jpegData then
            -- jpegData : string binaire du JPEG
            -- Écrire dans un fichier tmp et transmettre à l'App Python
            LrFileUtils.writeFile(
                LrPathUtils.child(LrPathUtils.getStandardFilePath('temp'), 'thumb.jpg'),
                jpegData
            )
        end
    end
)
```

### Contraintes Lua 5.1 spécifiques à Lr

```lua
-- PAS disponible en Lua 5.1 :
-- • Opérateur // (division entière) → utiliser math.floor(a/b)
-- • goto
-- • table.move, table.pack, table.unpack (remplacer par unpack())
-- • string.gmatch avec \d (utiliser [0-9])
-- • utf8 library
-- • Integer type distinct (tout est float)

-- Division entière
local n = math.floor(10 / 3)  -- 3

-- Unpack
local a, b, c = unpack({ 1, 2, 3 })

-- Nombre de bits dans un entier : 32 bits en Lua 5.1
```
