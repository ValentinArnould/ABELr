# Lightroom Classic — Référence API SDK (Lua)

> **Source** : SDK officiel **Adobe Lightroom Classic 15.2** (build 202602111402-ec4112e8).
> Reconstruit depuis `documentation/Lr_SDK_API/` :
> `API Reference/modules/*.html`, `Manual/Lightroom Classic SDK Guide.pdf`, `Sample Plugins/`.
>
> Toutes les signatures, valeurs énumérées et notes « First supported in version X » de ce fichier
> sont **vérifiées** dans la doc Adobe sauf mention `⚠️` explicite. La version d'introduction d'une
> méthode est indiquée quand elle est > 6.0 (utile car la cible projet est Lr 12+ = SDK 12+).
>
> - Langage : **Lua 5.1**. Import SDK : `import 'LrXxx'` (jamais `require` pour les modules SDK).
> - `require` reste valide pour **vos** modules locaux (`require 'lib.Foo'`) et libs embarquées (`require 'dkjson'`).

---

## Table des matières

1. [Imports SDK disponibles](#1-imports-sdk-disponibles)
2. [Détection plateforme & version](#2-détection-plateforme--version)
3. [LrApplication](#3-lrapplication)
4. [LrCatalog](#4-lrcatalog)
5. [LrPhoto — métadonnées](#5-lrphoto--métadonnées)
6. [LrPhoto — develop settings](#6-lrphoto--develop-settings)
7. [LrDevelopController](#7-lrdevelopcontroller)
8. [Paramètres Develop — noms SDK](#8-paramètres-develop--noms-sdk)
9. [Masques & Denoise/Enhance via SDK](#9-masques--denoiseenhance-via-sdk)
10. [LrSelection / LrApplicationView](#10-lrselection--lrapplicationview)
11. [LrTasks — asynchronisme](#11-lrtasks--asynchronisme)
12. [LrFunctionContext](#12-lrfunctioncontext)
13. [LrHttp](#13-lrhttp)
14. [LrSocket](#14-lrsocket)
15. [LrDialogs](#15-lrdialogs)
16. [LrProgressScope](#16-lrprogressscope)
17. [LrView — dialogs custom](#17-lrview--dialogs-custom)
18. [LrFileUtils / LrPathUtils](#18-lrfileutils--lrpathutils)
19. [LrStringUtils / LrColor / LrDigest / LrMD5](#19-lrstringutils--lrcolor--lrdigest--lrmd5)
20. [LrShell & processus externes](#20-lrshell--processus-externes)
21. [LrLogger — débogage](#21-lrlogger--débogage)
22. [LrPrefs / LrPlugin / LrErrors](#22-lrprefs--lrplugin--lrerrors)
23. [Info.lua — manifeste plugin](#23-infolua--manifeste-plugin)
24. [Patterns du projet](#24-patterns-du-projet)
25. [Limitations & contraintes](#25-limitations--contraintes)

---

## 1. Imports SDK disponibles

Tous les modules présents dans `API Reference/modules/` (SDK 15.2) :

```lua
-- Cœur catalogue / photo
local LrApplication       = import 'LrApplication'
local LrApplicationView   = import 'LrApplicationView'
local LrCatalog           -- pas d'import direct : via LrApplication.activeCatalog()
local LrPhoto             -- classe, pas de namespace importable
local LrSelection         = import 'LrSelection'

-- Develop
local LrDevelopController  = import 'LrDevelopController'
local LrDevelopPreset      = import 'LrDevelopPreset'
local LrDevelopPresetFolder= import 'LrDevelopPresetFolder'

-- Tâches / contexte / erreurs
local LrTasks             = import 'LrTasks'
local LrFunctionContext   = import 'LrFunctionContext'
local LrErrors            = import 'LrErrors'
local LrRecursionGuard    = import 'LrRecursionGuard'

-- Réseau / IPC
local LrHttp              = import 'LrHttp'
local LrSocket            = import 'LrSocket'
local LrFtp               = import 'LrFtp'

-- UI
local LrDialogs           = import 'LrDialogs'
local LrView              = import 'LrView'
local LrBinding           = import 'LrBinding'
local LrProgressScope     = import 'LrProgressScope'
local LrColor             = import 'LrColor'
local LrSounds            = import 'LrSounds'

-- Fichiers / chemins / chaînes / dates
local LrFileUtils         = import 'LrFileUtils'
local LrPathUtils         = import 'LrPathUtils'
local LrStringUtils       = import 'LrStringUtils'
local LrDate              = import 'LrDate'
local LrMath              = import 'LrMath'
local LrXml               = import 'LrXml'

-- Système / plugin / prefs / sécurité
local LrSystemInfo        = import 'LrSystemInfo'
local LrPrefs             = import 'LrPrefs'
local LrPasswords         = import 'LrPasswords'
local LrShell             = import 'LrShell'
local LrLogger            = import 'LrLogger'
local LrDigest            = import 'LrDigest'
local LrMD5               = import 'LrMD5'
local LrLocalization      = import 'LrLocalization'

-- Collections / mots-clés / dossiers
local LrCollection        -- classes obtenues via le catalog
local LrCollectionSet
local LrKeyword
local LrFolder

-- Export / publish (non requis pour ce projet)
local LrExportSession     = import 'LrExportSession'
local LrExportSettings    = import 'LrExportSettings'
```

> `LrCatalog`, `LrPhoto`, `LrCollection`, `LrKeyword`, `LrFolder`, etc. sont des **classes** :
> on obtient des instances depuis le catalogue, on n'importe pas le namespace.

---

## 2. Détection plateforme & version

`LrApplication.platform()` **n'existe pas**. Pour détecter l'OS :

```lua
-- Globals booléens définis par Lr (confirmé SDK Guide)
if WIN_ENV then  -- Windows
elseif MAC_ENV then  -- macOS
end

-- LrSystemInfo (SDK 3.0+) pour plus de détails
local LrSystemInfo = import 'LrSystemInfo'
-- (voir LrSystemInfo.html : architecture, mémoire, etc.)

-- Version Lr
local LrApplication = import 'LrApplication'
local v = LrApplication.versionString()    -- ex. "15.2"
local t = LrApplication.versionTable()
-- t.major, t.minor, t.revision, t.build_version (string), t.build (déprécié)
```

---

## 3. LrApplication

Namespace, fonctions appelées directement. **Aucune méthode `platform`/`cameraRawVersion`/`quit` simple** ;
la fermeture de l'app se fait via `LrApplication.shutdown()` (SDK 14.3+).

```lua
local LrApplication = import 'LrApplication'

-- Catalogue actif (SDK 1.3+)
local catalog = LrApplication.activeCatalog()        -- → LrCatalog

-- Version
LrApplication.versionString()                        -- "15.2"
LrApplication.versionTable()                         -- table {major,minor,revision,build_version,...}

-- Presets develop (utile pour appliquer un look existant)
LrApplication.developPresetFolders()                 -- array LrDevelopPresetFolder (3.0+)
LrApplication.developPresetByUuid(uuid)              -- LrDevelopPreset (3.0+)
LrApplication.addDevelopPresetForPlugin(_PLUGIN, name, settingsTable)  -- 3.0+
LrApplication.getDevelopPresetsForPlugin(_PLUGIN, uuid)               -- 3.0+

-- Presets divers (table {nom = uuid})
LrApplication.metadataPresets()                      -- 3.0+
LrApplication.filenamePresets()                      -- 3.0+
LrApplication.viewFilterPresets()                    -- 3.0+

-- Identifiants machine / licence (enregistrement plugin)
LrApplication.serialNumberHash()                     -- 3.0+
LrApplication.macAddressHash()                       -- 4.1+
LrApplication.purchaseSource()                       -- 'retail' | 'MAS' | 'CC'

-- Divers
LrApplication.backupAtNextShutdown(_PLUGIN.id)       -- 4.0+
LrApplication.shutdown()                             -- 14.3+ (quitte Lr)
```

---

## 4. LrCatalog

Obtenu via `LrApplication.activeCatalog()`. La plupart des lectures (getAllPhotos, find*, getKeywords…)
**doivent tourner dans une tâche** `LrTasks`. Les écritures **doivent** être dans `withWriteAccessDo`.

### Sélection / accès photos

```lua
local catalog = LrApplication.activeCatalog()

catalog:getTargetPhotos()    -- array LrPhoto : sélection, sinon tout le filmstrip (3.0+)
catalog:getTargetPhoto()     -- LrPhoto active (la plus sélectionnée) ou nil (3.0+)
catalog:getMultipleSelectedOrAllPhotos()  -- sélection si >1, sinon toutes les visibles (3.0+)
catalog:getAllPhotos()       -- array de TOUTES les photos du catalogue (3.0+, dans une task)

catalog:setSelectedPhotos(activePhoto, { otherPhotos })  -- 3.0+
```

### Recherche

```lua
catalog:findPhotoByPath(absolutePath, caseSensitivity)   -- LrPhoto ou nil (2.0+, dans une task)
catalog:findPhotoByUuid(uuid)                             -- LrPhoto ou nil (2.0+, dans une task)
catalog:findPhotos{ sort=, ascending=, searchDesc={ criteria=, operation=, value= } }  -- 2.0+
-- searchDesc supporte combine = "union"|"intersect"|"exclude" + critères imbriqués.
-- criteria utiles : "rating","pick","labelColor","fileFormat","camera","isoSpeedRating",
--   "captureTime","hasAdjustments","cropped","aspectRatio","keywords","folder","collection"…
-- fileFormat enum : "DNG","RAW","JPG","TIFF","PNG","PSD","VIDEO","PSB","AVIF","JXL"
```

### Lecture batch (efficace pour 500-1000 photos)

```lua
catalog:batchGetRawMetadata(photos, keys)        -- table { [photo] = {key=val,...} } (3.0+)
catalog:batchGetFormattedMetadata(photos, keys)  -- idem, valeurs formatées (3.0+)
-- keys = nil → tous les champs disponibles.
```

### Transactions d'écriture

```lua
-- Écriture standard (entre dans la pile d'annulation Undo). NE PAS imbriquer.
catalog:withWriteAccessDo('Nom action Undo', function(context)
    -- modifications catalogue / develop settings ici
end, timeoutParams)   -- timeoutParams optionnel {timeout=, callback=, asynchronous=}

-- Écriture des seules métadonnées plugin, hors pile Undo
catalog:withPrivateWriteAccessDo(function(context) ... end, timeoutParams)

-- Écriture longue avec dialog d'avertissement + progress (gros batch)
catalog:withProlongedWriteAccessDo{
    title = 'Lr Automation', pluginName = 'Lr Automation',
    func = function(context, progressScope) ... end,
}

-- Propriétés (read-only) pour vérifier le contexte
catalog.hasWriteAccess          -- bool
catalog.hasPrivateWriteAccess   -- bool
catalog:getPath()               -- chemin absolu du .lrcat (3.0+)
```

> **Important (3.0+)** : les objets créés dans un `withWriteAccessDo` (collections, etc.) ne sont
> accessibles **qu'après** la fin du callback. Les `with___AccessDo` imbriqués échouent.
> Plusieurs `withWriteAccessDo` successifs sans interaction utilisateur sont fusionnés en un seul Undo.

### Autres (collections, mots-clés, import)

```lua
catalog:createCollection(name, parentSet, canReturnPrior)       -- 3.0+ (dans writeAccess)
catalog:createCollectionSet(name, parentSet, canReturnPrior)    -- 3.0+
catalog:createSmartCollection(name, searchDesc, parent, canReturnPrior)
catalog:getChildCollections() / :getChildCollectionSets()       -- (dans une task)
catalog:createKeyword(name, synonyms, includeOnExport, parent, returnExisting)
catalog:getKeywords()                                           -- (dans une task)
catalog:getFolders() / :getFolderByPath(path)
catalog:addPhoto(path, stackWith, position, metaPresetUUID, developPresetUUID)  -- 2.0+ (12.5 pour presets)
catalog:buildSmartPreviews(photos)                             -- 5.0+ (dans une task)
catalog:setActiveSources(sources) / :getActiveSources()
catalog:updateAISettings(photos)                              -- 13.3+ (dans writeAccess)
catalog:deleteAllEmptyMasks(photos)                          -- 14.0+ (dans writeAccess)
```

---

## 5. LrPhoto — métadonnées

Instances issues de `catalog:getTargetPhotos()` etc. Les lectures `getRawMetadata`/`getFormattedMetadata`
**doivent tourner dans une tâche** `LrTasks` (depuis 3.0 plus besoin de write-access pour lire).

### `photo:getRawMetadata(key)` — valeurs brutes (typées)

```lua
-- Fichier / identité
photo:getRawMetadata('path')          -- string : chemin absolu actuel (ou dernier connu) (3.0+)
photo:getRawMetadata('uuid')          -- string : ID persistant (3.0+)
photo.localIdentifier                  -- number : ID local catalogue (propriété, 4.0+)
photo:getRawMetadata('fileSize')      -- number (octets)
photo:getRawMetadata('fileFormat')    -- 'RAW','DNG','JPG','PSD','TIFF','VIDEO' (2.0+)
photo:getRawMetadata('isVideo')       -- bool (3.0+)
photo:getRawMetadata('bitDepth')      -- number (12.1+)

-- Dimensions
photo:getRawMetadata('dimensions')        -- { width=, height= }
photo:getRawMetadata('croppedDimensions') -- { width=, height= }
photo:getRawMetadata('width') / ('height')-- number (2.0+)
photo:getRawMetadata('aspectRatio')       -- number = width/height (2.0+)
photo:getRawMetadata('isCropped')         -- bool (2.0+)

-- EXIF
photo:getRawMetadata('isoSpeedRating')    -- number (ex. 200)
photo:getRawMetadata('aperture')          -- number : dénominateur f (ex. 2.8)
photo:getRawMetadata('shutterSpeed')      -- number : secondes (1/60 = 0.01666)
photo:getRawMetadata('focalLength')       -- number : mm
photo:getRawMetadata('focalLength35mm')   -- number : mm équivalent 35mm
photo:getRawMetadata('exposureBias')      -- number (ex. -0.6666)
photo:getRawMetadata('flash')             -- bool ou nil
photo:getRawMetadata('dateTimeOriginalISO8601')  -- string ISO 8601 (2.0+, fiable)
photo:getRawMetadata('gps')               -- { latitude=, longitude= } ou nil
photo:getRawMetadata('gpsAltitude')       -- number (m)

-- Classement
photo:getRawMetadata('rating')            -- number 0-5 ou nil
photo:getRawMetadata('pickStatus')        -- 1 pick / 0 neutre / -1 reject (4.0+)
photo:getRawMetadata('colorNameForLabel') -- 'red','yellow','green','blue','purple','none'

-- Copies virtuelles / piles
photo:getRawMetadata('isVirtualCopy')     -- bool
photo:getRawMetadata('countVirtualCopies')-- number
photo:getRawMetadata('masterPhoto')       -- LrPhoto (si copie virtuelle)

-- Smart preview (utile si RAW hors-ligne)
photo:getRawMetadata('smartPreviewInfo')  -- { smartPreviewPath=, smartPreviewSize= } (5.0+)

-- Mots-clés / custom
photo:getRawMetadata('keywords')          -- array LrKeyword (3.0+)
photo:getRawMetadata('customMetadata')    -- table (3.0+)
photo:getRawMetadata('isExported')        -- bool (13.3+)
```

> ⚠️ Le nom **caméra** n'est PAS dans `getRawMetadata`. Le modèle vient de
> `getFormattedMetadata('cameraModel')` / `('cameraMake')`. Idem `lens`.

### `photo:getFormattedMetadata(key)` — chaînes affichables (ne pas parser)

```lua
photo:getFormattedMetadata('cameraModel')   -- ex. "ILCE-7M4"
photo:getFormattedMetadata('cameraMake')    -- ex. "SONY"
photo:getFormattedMetadata('lens')          -- ex. "FE 85mm F1.8"
photo:getFormattedMetadata('fileName')      -- "DSC00123.ARW"
photo:getFormattedMetadata('fileType')      -- "Raw" / "DNG" / …
photo:getFormattedMetadata('exposure')      -- "1/200 sec at f/2.8"
photo:getFormattedMetadata('isoSpeedRating')-- "ISO 800"
photo:getFormattedMetadata('focalLength')   -- "85 mm"
photo:getFormattedMetadata('title') / ('caption') / ('label')
photo:getFormattedMetadata('croppedDimensions')  -- "3072 x 2304"
-- key = nil → table de tous les champs.
```

### `photo:setRawMetadata(key, value)` — dans `withWriteAccessDo`

```lua
catalog:withWriteAccessDo('Set metadata', function()
    photo:setRawMetadata('rating', 5)            -- number
    photo:setRawMetadata('label', 'red')         -- nom du label couleur
    photo:setRawMetadata('colorNameForLabel', 'red')
    photo:setRawMetadata('pickStatus', 1)        -- 1 / 0 / -1 (4.0+)
    photo:setRawMetadata('title', 'Titre')
    photo:setRawMetadata('caption', 'Légende')
    photo:setRawMetadata('gps', { latitude=35.1, longitude=86.7 })  -- 4.0+
end)
```

> EXIF (ISO, vitesse, ouverture, focale, modèle…) sont **lecture seule** — `setRawMetadata`
> n'accepte que rating/label/pick/gps/titre/légende et champs IPTC (voir LrPhoto.html).

### Autres méthodes LrPhoto utiles

```lua
photo:getDevelopSettings()                   -- table complète (3.0+, dans une task) — voir §6
photo:applyDevelopSettings(settings, optHistoryName, optFlattenAutoNow)  -- 6.0+, writeAccess
photo:applyDevelopPreset(preset, _PLUGIN, presetAmount, updateAISettings)-- 3.0+, writeAccess
photo:applyDevelopSnapshot(id) / :createDevelopSnapshot(name, updateInPlace) / :getDevelopSnapshots()
photo:requestJpegThumbnail(w, h, function(jpeg, err) ... end)  -- 5.0+, dans une task
photo:checkPhotoAvailability()               -- bool : fichier présent ? (2.0+, dans une task)
photo:buildSmartPreview() / :deleteSmartPreview()              -- 5.0+
photo:addKeyword(kw) / :removeKeyword(kw)    -- writeAccess
photo:getPropertyForPlugin(_PLUGIN, fieldId, optVersion, noThrow)  -- métadonnées custom
photo:setPropertyForPlugin(_PLUGIN, fieldId, value)               -- writeAccess
photo:type()                                 -- 'LrPhoto'
photo.catalog                                -- LrCatalog parent
```

---

## 6. LrPhoto — develop settings

`photo:getDevelopSettings()` (3.0+, **dans une task**) retourne une grande table.
⚠️ Adobe précise : *« The develop settings APIs are considered experimental »* — ne pas dépendre d'une
clé absente ; la liste de référence reste l'UI. Membres confirmés (extrait) :

```
WhiteBalance(string) Temperature Tint
Exposure Contrast Highlights Shadows Whites Blacks  (+ variantes *2012 : Exposure2012, Highlights2012…)
Clarity Dehaze Vibrance Saturation Brightness
HueAdjustment{Red,Orange,Yellow,Green,Aqua,Blue,Purple,Magenta}
SaturationAdjustment{...}  LuminanceAdjustment{...}   GrayMixer{...} (N&B)
ParametricShadows/Darks/Lights/Highlights + *Split   ToneCurvePV2012(+Red/Green/Blue)  ToneCurveName2012
SplitToningShadowHue/Saturation  SplitToningHighlightHue/Saturation  SplitToningBalance
ColorGradeMidtoneHue/Sat/Lum  ColorGradeGlobalHue/Sat/Lum  ColorGradeShadowLum  ColorGradeHighlightLum  ColorGradeBlending
Sharpness SharpenRadius SharpenDetail SharpenEdgeMasking
LuminanceSmoothing LuminanceNoiseReductionDetail/Contrast  ColorNoiseReduction(+Detail)
GrainAmount GrainSize GrainFrequency
PostCropVignette{Amount,Midpoint,Feather,Roundness,Style,HighlightContrast}  VignetteAmount VignetteMidpoint
CameraProfile  RedHue/Saturation GreenHue/Saturation BlueHue/Saturation ShadowTint  EnableCalibration
CropTop/Left/Bottom/Right CropAngle  orientation
Enable* (flags bool) : EnableColorAdjustments, EnableDetail, EnableEffects, EnableLensCorrections, EnableTransform…
ProcessVersion(string)  PointColors(table 13.0+)  LensBlur(table) DepthMapInfo(table)
HDREditMode HDRMaxValue SDRBrightness/Contrast/Clarity/Highlights/Shadows/Whites/Blacks/Blend  (13.0+)
MaskGroupBasedCorrections(table 11.0+)
```

Appliquer en batch (pas besoin du module Développement) :

```lua
catalog:withWriteAccessDo('Apply adjustments', function()
    photo:applyDevelopSettings({
        Exposure    = 0.35,   -- voir §8 pour les plages
        Temperature = 5600,
        Tint        = -5,
        Highlights  = -20,
        Shadows     = 15,
    }, 'Lr Automation')       -- 2e arg = nom du pas d'historique (optionnel)
end)
```

> `applyDevelopSettings` fonctionne sur n'importe quelle photo (pas besoin du module Develop actif),
> contrairement à `LrDevelopController` (§7). C'est **la** voie pour le traitement batch.

---

## 7. LrDevelopController

Namespace. Opère sur la **photo active dans le module Développement uniquement** : la plupart des
fonctions exigent *« Must be called while the Develop module is active »*. Pour du batch, préférer
`photo:applyDevelopSettings()` (§6). Utile ici surtout pour `getRange`, l'auto-tone et l'Enhance/Denoise.

```lua
local LrDevelopController = import 'LrDevelopController'

-- Lecture / écriture d'un paramètre (6.0+)
local val = LrDevelopController.getValue('Exposure')
LrDevelopController.setValue('Exposure', 0.5, withClippingOn)   -- 3e arg optionnel (overlay clipping)
LrDevelopController.increment('Exposure') / .decrement('Exposure')
LrDevelopController.resetToDefault('Exposure')
LrDevelopController.resetAllDevelopAdjustments()

-- Plage réelle d'un paramètre (à l'exécution) — précieux car le SDK ne documente pas les bornes
local mn, mx = LrDevelopController.getRange('Exposure')         -- 6.0+

-- Auto
LrDevelopController.setAutoTone()           -- 7.4+
LrDevelopController.setAutoWhiteBalance()    -- 7.4+

-- Process version
LrDevelopController.getProcessVersion()
LrDevelopController.setProcessVersion('Version 6')  -- "Version 1".."Version 6"

-- Outils / panneaux
LrDevelopController.selectTool('crop')       -- "loupe","crop","dust","redeye","masking","upright",
                                             --  "point_color","local_point_color","depth_refinement"
LrDevelopController.getSelectedTool()
LrDevelopController.revealPanel('adjustPanel')        -- déplie un panneau
LrDevelopController.revealPanelIfVisible('tonePanel')

-- Réglages de comportement (éviter trop d'états d'historique en boucle)
LrDevelopController.setTrackingDelay(seconds)
LrDevelopController.setMultipleAdjustmentThreshold(seconds)   -- défaut 0.5 s
LrDevelopController.startTracking('Exposure') / .stopTracking()

-- Observer les changements (UI)
LrDevelopController.addAdjustmentChangeObserver(context, observer, function(obs) ... end)  -- 6.0+
```

> ⚠️ `Temperature` est **logarithmique** pour RAW/DNG via `setValue` (le reste est linéaire).
> `Texture` désactivé en Process Version 1 & 2.

Panneaux valides pour `revealPanel` / noms de groupes de paramètres :
`adjustPanel, tonePanel, mixerPanel, colorGradingPanel, detailPanel, lensCorrectionsPanel,
effectsPanel, calibratePanel, lensBlurPanel`.

---

## 8. Paramètres Develop — noms SDK

Noms utilisables dans `photo:applyDevelopSettings({})` et `LrDevelopController.setValue()`.
**Les noms sont confirmés** (liste `LrDevelopController` + table `getDevelopSettings`). Les **plages**
ci-dessous sont les plages UI Camera Raw usuelles (le SDK ne les fige pas — interroger
`LrDevelopController.getRange(param)` à l'exécution pour les bornes exactes).

### Exposition & tonalité
| Param | Plage UI | Note |
|---|---|---|
| `Exposure` | −5.0 … +5.0 | stops |
| `Contrast` | −100 … +100 | |
| `Highlights` `Shadows` `Whites` `Blacks` | −100 … +100 | |
| `Clarity` `Dehaze` `Texture` | −100 … +100 | |
| `Brightness` | −150 … +150 | Process Version 1/2 seulement |

### Balance des blancs
| Param | Valeur | Note |
|---|---|---|
| `Temperature` | 2000 … 50000 (K) | logarithmique pour RAW |
| `Tint` | −150 … +150 | |
| `WhiteBalance` | string | `'As Shot'`,`'Auto'`,`'Custom'`,`'Daylight'`,`'Cloudy'`,`'Shade'`,`'Tungsten'`,`'Fluorescent'`,`'Flash'` |

### Couleur globale
`Vibrance`, `Saturation` : −100 … +100.

### HSL / TSL (8 canaux : Red, Orange, Yellow, Green, Aqua, Blue, Purple, Magenta)
| Préfixe SDK | Plage |
|---|---|
| `HueAdjustment<Canal>` | −100 … +100 |
| `SaturationAdjustment<Canal>` | −100 … +100 |
| `LuminanceAdjustment<Canal>` | −100 … +100 |
| `GrayMixer<Canal>` | −100 … +100 (mélange N&B) |

### Color Grading / Étalonnage couleur (Process Version 3+)
> ⚠️ Hybride : **ombres** et **hautes lumières** utilisent les noms `SplitToning*` pour Hue/Sat,
> mais `ColorGrade*Lum` pour la luminance. Tons moyens & global utilisent `ColorGrade*`.

| Zone | Hue | Saturation | Luminance |
|---|---|---|---|
| Ombres | `SplitToningShadowHue` | `SplitToningShadowSaturation` | `ColorGradeShadowLum` |
| Hautes lumières | `SplitToningHighlightHue` | `SplitToningHighlightSaturation` | `ColorGradeHighlightLum` |
| Tons moyens | `ColorGradeMidtoneHue` | `ColorGradeMidtoneSat` | `ColorGradeMidtoneLum` |
| Global | `ColorGradeGlobalHue` | `ColorGradeGlobalSat` | `ColorGradeGlobalLum` |

Plages : Hue 0…360, Sat 0…100, Lum −100…+100. Plus : `SplitToningBalance` (−100…+100),
`ColorGradeBlending` (0…100).

### Courbe paramétrique
`ParametricShadows`, `ParametricDarks`, `ParametricLights`, `ParametricHighlights` : −100…+100.
Points de bascule : `ParametricShadowSplit`, `ParametricMidtoneSplit`, `ParametricHighlightSplit`.
Courbe par points : table `ToneCurvePV2012` (+ `…Red/Green/Blue`).

### Détail (netteté / bruit)
| Param | Plage |
|---|---|
| `Sharpness` | 0 … 150 |
| `SharpenRadius` | 0.5 … 3.0 |
| `SharpenDetail` | 0 … 100 |
| `SharpenEdgeMasking` | 0 … 100 |
| `LuminanceSmoothing` | 0 … 100 |
| `LuminanceNoiseReductionDetail` / `…Contrast` | 0 … 100 |
| `ColorNoiseReduction` (+`Detail`, +`Smoothness`) | 0 … 100 |

> **Denoise IA** : pas un paramètre de `applyDevelopSettings`. Voir §9 (`LrDevelopController.toggleEnhance` /
> `changeDenoiseAmount`, module Develop actif).

### Corrections optiques / géométrie
| Param | Valeur |
|---|---|
| `LensProfileEnable` | 0 / 1 |
| `AutoLateralCA` | 0 / 1 |
| `VignetteAmount` / `VignetteMidpoint` | vignettage optique |
| `DefringePurpleAmount` / `DefringeGreenAmount` (+ HueLo/Hi) | aberration couleur |
| `PerspectiveVertical/Horizontal/Rotate/Scale/Aspect/X/Y` | transform manuel |
| `PerspectiveUpright` | mode Upright |
| `straightenAngle` / `CropAngle` | redressement |

### Effets
`PostCropVignetteAmount/Midpoint/Feather/Roundness/Style/HighlightContrast`,
`GrainAmount` (0…100), `GrainSize` (0…100), `GrainFrequency` (0…100).

### Recadrage
`CropTop`, `CropLeft`, `CropBottom`, `CropRight` : proportions 0.0…1.0. `CropAngle` : −45…+45.

### Calibration caméra
`CameraProfile` (string, ex. `'Camera Standard'`, `'Adobe Standard'`), `ShadowTint`,
`RedHue`/`RedSaturation`, `GreenHue`/`GreenSaturation`, `BlueHue`/`BlueSaturation` (−100…+100),
`EnableCalibration` (bool).

### ProcessVersion
Strings valides : `"Version 1"` … `"Version 6"` (via `LrDevelopController.setProcessVersion`).

---

## 9. Masques & Denoise/Enhance via SDK

Contrairement aux anciennes versions, le SDK 11+ expose le **masquage** et le SDK 14.5 l'**Enhance**.
Toutes ces fonctions exigent le **module Développement actif** (et souvent l'outil ouvert).

### Masques (LrDevelopController, 11.0+)
```lua
LrDevelopController.goToMasking()
LrDevelopController.createNewMask(maskType, maskSubtype)
LrDevelopController.addToCurrentMask(maskType, maskSubtype)
LrDevelopController.subtractFromCurrentMask(...) / .intersectWithCurrentMask(...)
LrDevelopController.getAllMasks() / .getSelectedMask() / .selectMask(id)
LrDevelopController.invertMask(id) / .duplicateAndInvertMask(id) / .deleteMask(id)
```
- `maskType` : `"brush"`, `"gradient"`, `"radialGradient"`, `"rangeMask"`, `"aiSelection"`.
- `maskSubtype` (pour rangeMask/aiSelection) : `"color"`, `"luminance"`, `"depth"`, `"subject"`,
  `"sky"`, `"background"`, `"objects"`, `"people"`, `"landscape"`.

> Les masques IA (Sujet, Ciel, Arrière-plan, Personnes…) **sont** créables via `createNewMask("aiSelection", subtype)`.
> Mais **lire** la géométrie/pixels d'un masque reste impossible.

### Enhance : Denoise IA / Raw Details / Super Resolution (14.5+)
```lua
LrDevelopController.toggleEnhance('denoise', denoiseAmount, callback, args)  -- 'denoise'|'rawDetails'|'superRes'
LrDevelopController.changeDenoiseAmount(amount)        -- 1..100
LrDevelopController.getEnhancePanelState()             -- table {denoiseAmount, denoiseEnabled,...}
```

### Remove / Reflection / Distracting people (14.1–14.5)
`goToRemove(spotType, whichFeature)`, `setRemovePanelPreferences{...}`, `getAllSpots`,
`toggleReflectionRemoval(amount, quality)`, `detectDistractingPeople()`, etc. (voir LrDevelopController.html).

### Point Color (13.2+, Process Version 3+)
`addPointColorSwatch`, `selectPointColorSwatch(1..8)`, `updateSelectedPointColorSwatch`,
`getValue('PointColors')` / `getValue('local_PointColors')`.

---

## 10. LrSelection / LrApplicationView

### LrSelection (6.0+) — agit sur la sélection (grille) ou la photo active
```lua
local LrSelection = import 'LrSelection'
LrSelection.getRating() / .setRating(0..5) / .increaseRating() / .decreaseRating()
LrSelection.getFlag()                 -- -1 reject / 0 none / 1 pick
LrSelection.flagAsPick() / .flagAsReject() / .removeFlag()
LrSelection.getColorLabel()           -- "red","yellow","green","blue","purple","other","none"
LrSelection.setColorLabel('red')      -- "red"|"yellow"|"green"|"blue"|"purple"|"none"
LrSelection.selectAll() / .selectNone() / .selectInverse()
LrSelection.nextPhoto() / .previousPhoto() / .extendSelection('left'|'right', n)
LrSelection.deselectActive() / .deselectOthers()
LrSelection.removeFromCatalog()       -- 14.3+
```

### LrApplicationView — état des vues / modules
```lua
local LrApplicationView = import 'LrApplicationView'
LrApplicationView.getCurrentModuleName()    -- "library","develop","map","book","slideshow","print","web"
LrApplicationView.switchToModule('develop') -- nécessaire avant d'utiliser LrDevelopController
LrApplicationView.showView('grid')          -- "loupe","grid","compare","survey","develop_loupe",…
LrApplicationView.zoomIn() / .zoomOut() / .toggleZoom() / .zoomToOneToOne()
LrApplicationView.isSecondaryDisplayOn() / .showSecondaryView('loupe')
```

> Pour piloter `LrDevelopController` sur une photo précise : `catalog:setSelectedPhotos(photo,{})`
> puis `LrApplicationView.switchToModule('develop')`.

---

## 11. LrTasks — asynchronisme

Tout I/O bloquant (HTTP, sleep, gros fichiers) doit tourner dans une tâche coopérative.
Pas de vrai multithreading : ce sont des coroutines sur le thread principal.

```lua
local LrTasks = import 'LrTasks'

LrTasks.startAsyncTask(function() ... end, 'optName')   -- 1.3+ ; affiche un dialog d'erreur si throw
LrTasks.startAsyncTaskWithoutErrorHandler(function() ... end)  -- sans dialog auto
LrTasks.sleep(0.3)        -- secondes (float)
LrTasks.yield()           -- rend la main brièvement (à appeler dans les longues boucles)
LrTasks.canYield()        -- bool : peut-on yield ici ?
LrTasks.pcall(func, ...)  -- pcall yield-safe
LrTasks.execute(cmd)      -- comme os.execute mais ne bloque que la tâche → exit code (number)
```

> `LrTasks.execute` est la voie recommandée pour lancer un process externe sans figer Lr
> (préférable à `io.popen`/`os.execute` purs). Voir §20.

---

## 12. LrFunctionContext

Nettoie les ressources à la fin d'une fonction/tâche. **Obligatoire** pour `LrHttp.post`,
les property tables observables (`LrBinding`), `LrProgressScope`, `LrSocket`.

```lua
local LrFunctionContext = import 'LrFunctionContext'

LrFunctionContext.callWithContext('nom', function(context) ... end, ...)   -- 1.3+
LrFunctionContext.pcallWithContext('nom', function(context) ... end)       -- variante protégée
LrFunctionContext.postAsyncTaskWithContext('nom', function(context) ... end) -- task + context

-- Sur l'objet context :
context:addCleanupHandler(function(success, ...) ... end)   -- appelé à la fin (ordre inverse)
context:addFailureHandler(function(false, msg) ... end)     -- appelé seulement si erreur
context:addOperationTitleForError('Échec de l’opération.')
```

---

## 13. LrHttp

**Uniquement dans une tâche asynchrone.** En cas d'erreur réseau, les méthodes retournent `nil`
+ un objet info contenant `info.error.errorCode` (`"timedOut"`, `"cannotConnectToHost"`,
`"cannotFindHost"`, `"networkConnectionLost"`, `"cancelled"`, …).

```lua
local LrHttp = import 'LrHttp'

-- GET (1.3+) — utilisable dans toute task
local body, headers = LrHttp.get(url, headersTable, timeout)
-- headersTable : { { field='X', value='Y' }, ... } ; timeout en secondes (optionnel)
-- headers.status = code HTTP (integer). headers = nil sur erreur réseau.

-- POST (1.3+) — DOIT être appelé depuis LrFunctionContext.postAsyncTaskWithContext()
local body, headers = LrHttp.post(url, postBody, headersTable, method, timeout, totalSize)
-- method optionnel (défaut "POST"). postBody : string (ou fonction fournissant des chunks, 4.1+).

-- POST multipart (upload fichier)
local body, headers = LrHttp.postMultipart(url, content, headers, timeout, callbackFn, suppressFormData)
-- content : { { name=, filePath=, fileName=, contentType= }, { name=, value= } }

LrHttp.openUrlInBrowser(url)            -- ouvre dans le navigateur
LrHttp.parseCookie(setCookieValue)      -- parse un header Set-Cookie
```

> **Content-Type** : si non spécifié, Lr ajoute `text/plain`. Pour du JSON, passer
> `{ field='Content-Type', value='application/json' }`. Pour forcer l'absence : valeur `'skip'`.
>
> ⚠️ Détail important : `LrHttp.post` exige le contexte `postAsyncTaskWithContext`. `LrHttp.get`
> fonctionne dans n'importe quelle `startAsyncTask`. Pour la boucle de polling (GET fréquent +
> POST occasionnel des résultats), envelopper l'envoi des résultats dans `postAsyncTaskWithContext`.

Vérifier le statut :

```lua
if not headers then
    -- App non démarrée / erreur réseau
elseif headers.status == 200 then
    -- OK
end
```

---

## 14. LrSocket

Sockets localhost (6.0+) pour IPC bidirectionnelle. Non requis pour ce projet (LrHttp suffit),
mais c'est l'alternative si l'on veut que l'App **pousse** vers le plugin sans polling.
Fermé automatiquement si le plugin est désactivé/supprimé.

```lua
local LrSocket = import 'LrSocket'
LrFunctionContext.callWithContext('sock', function(context)
    local sender = LrSocket.bind {
        functionContext = context,
        plugin = _PLUGIN,
        port = 0,             -- 0 = port choisi par l'OS
        mode = 'send',        -- 'send' | 'receive'
        onConnected = function(socket, port) end,
        onMessage   = function(socket, message) end,   -- mode 'receive'
        onClosed    = function(socket) end,
        onError     = function(socket, err) if err=='timeout' then socket:reconnect() end end,
    }
    sender:send('Hello')      -- mode 'send'
    sender:close()
end)
```

> Exemples complets dans `Sample Plugins/remote_control_socket*.lrdevplugin/`.

---

## 15. LrDialogs

```lua
local LrDialogs = import 'LrDialogs'

LrDialogs.message(message, info, style)   -- style : "warning"(défaut) | "info" | "critical"
LrDialogs.showError(errorString)
LrDialogs.showBezel(message, fadeDelay)   -- toast fugace (5.0+)

-- Confirmation → "ok" | "cancel" | "other"
local r = LrDialogs.confirm(message, info, actionVerb, cancelVerb, otherVerb)

-- Dialog modal custom (LrView) → return value du bouton
local r = LrDialogs.presentModalDialog{
    title = '...', contents = viewHierarchy,
    actionVerb = 'OK', cancelVerb = 'Annuler',  -- cancelVerb = "  " (3 espaces) pour cacher Annuler
    otherVerb = nil, resizable = false,
}
LrDialogs.presentFloatingDialog(_PLUGIN, { title=, contents=, blockTask=, selectionChangeObserver= })

-- Sélecteurs de fichiers
local paths = LrDialogs.runOpenPanel{ title=, canChooseFiles=true, canChooseDirectories=false,
                                      allowsMultipleSelection=true, initialDirectory= }  -- array|nil
local path  = LrDialogs.runSavePanel{ title=, requiredFileType='json' }                 -- string|nil

-- Progress modal (bloquant) → LrProgressScope
local scope = LrDialogs.showModalProgressDialog{ title=, caption=, functionContext=, cannotCancel= }

-- Do-not-show
LrDialogs.messageWithDoNotShow{ message=, info=, actionPrefKey= }
LrDialogs.promptForActionWithDoNotShow{ message=, actionPrefKey=, verbBtns={ {label=,verb=} } }
```

---

## 16. LrProgressScope

```lua
local LrProgressScope = import 'LrProgressScope'

LrFunctionContext.callWithContext('analyse', function(context)
    local progress = LrProgressScope{
        title = 'Lr Automation — Analyse',
        functionContext = context,    -- terminé auto à la fin du contexte
        caption = 'Initialisation…',
    }
    local photos, total = catalog:getTargetPhotos(), #catalog:getTargetPhotos()
    for i, photo in ipairs(photos) do
        if progress:isCanceled() then break end
        progress:setCaption(('Photo %d/%d'):format(i, total))
        progress:setPortionComplete(i - 1, total)
        LrTasks.yield()
    end
    progress:done()
end)
```

Méthodes : `setPortionComplete(done, total)`, `getPortionComplete()`, `setCaption(s)`,
`setIndeterminate()`, `isCanceled()`, `setCancelable(bool)`, `cancel()`, `pause()`/`isPaused()` (7.5+),
`done()`. Scopes imbriquables via `parent=` / `parentEndRange=`.

---

## 17. LrView — dialogs custom

```lua
local LrView    = import 'LrView'
local LrBinding = import 'LrBinding'
local f = LrView.osFactory()

-- Property table observable (DOIT être créée dans un function context)
LrFunctionContext.callWithContext('dlg', function(context)
    local props = LrBinding.makePropertyTable(context)
    props.port = 5000

    local c = f:column {
        spacing = f:dialog_spacing(),
        f:row { f:static_text { title = 'Port :' },
                f:edit_field { value = LrView.bind('port'), width_in_chars = 6 } },
        f:checkbox  { title = 'Option', value = LrView.bind('flag') },
        f:separator { fill_horizontal = 1 },
        f:push_button { title = 'Action', action = function() ... end },
    }
    LrDialogs.presentModalDialog{ title = 'Lr Automation', contents = c }
end)
```

Contrôles courants (voir `LrView*.html`) : `static_text`, `edit_field`, `checkbox`, `radio_button`,
`popup_menu`, `combo_box`, `slider`, `push_button`, `password_field`, `picture`, `catalog_photo`.
Conteneurs : `row`, `column`, `group_box`, `scrolled_view`, `tab_view`, `view`. Liaison via
`LrView.bind('clé')` (deux sens) sur une property table observable.

---

## 18. LrFileUtils / LrPathUtils

### LrPathUtils — manipulation de chemins (toujours via ce module sous Windows)
```lua
local LrPathUtils = import 'LrPathUtils'
LrPathUtils.child(path, child)            -- joindre  'C:\d' + 'f' → 'C:\d\f'
LrPathUtils.parent(path)                  -- dossier parent (nil pour racine, 2.0+)
LrPathUtils.leafName(path)                -- dernier composant
LrPathUtils.extension(path)               -- 'ARW' (sans point), '' si aucune
LrPathUtils.removeExtension(path)
LrPathUtils.addExtension(path, ext) / .replaceExtension(path, ext)
LrPathUtils.isAbsolute(path) / .isRelative(path)
LrPathUtils.makeAbsolute(path, base) / .makeRelative(path, base)
LrPathUtils.standardizePath(path)         -- résout .. et ~
LrPathUtils.getStandardFilePath(which)    -- 'home','temp','desktop','appPrefs','pictures','documents','appData'
LrPathUtils.maxPathLength()
```

### LrFileUtils — fichiers/dossiers
```lua
local LrFileUtils = import 'LrFileUtils'
LrFileUtils.exists(path)            -- 'file' | 'directory' | false
LrFileUtils.readFile(path)          -- string (préférer à io pour chemins non-ASCII)
LrFileUtils.copy(src, dst) / .move(src, dst)      -- dossier parent dst doit exister
LrFileUtils.delete(path)            -- suppression immédiate (préférer moveToTrash)
LrFileUtils.moveToTrash(path)
LrFileUtils.createDirectory(path) / .createAllDirectories(path)   -- récursif
LrFileUtils.chooseUniqueFileName(path)
LrFileUtils.fileAttributes(path)    -- { fileSize, fileCreationDate, fileModificationDate }
LrFileUtils.isReadable/isWritable/isDeletable(path)
LrFileUtils.makeFileWritable(path)
-- Itérateurs (for ... do ; NE PAS break) :
for p in LrFileUtils.files(dir) do end
for p in LrFileUtils.directoryEntries(dir) do end
for p in LrFileUtils.recursiveFiles(dir) do end
for p in LrFileUtils.recursiveDirectoryEntries(dir) do end
```

> ⚠️ **Il n'existe PAS de `LrFileUtils.writeFile`.** Pour écrire un fichier, utiliser le `io`
> standard de Lua :
> ```lua
> local fh = io.open(path, 'w'); fh:write(content); fh:close()
> ```

---

## 19. LrStringUtils / LrColor / LrDigest / LrMD5

### LrStringUtils (UTF-8)
```lua
local S = import 'LrStringUtils'
S.trimWhitespace(s)
S.lower(s) / S.upper(s)              -- casse localisée (gère non-ASCII, contrairement à string.lower)
S.numberToString(n, precision) / S.numberToStringWithSeparators(n, precision)
S.byteString(n, precision)          -- "1.90 MB"
S.encodeBase64(s) / S.decodeBase64(s)
S.isOnlyAscii(s)
S.truncate(s, maxBytes)             -- coupe en préservant l'UTF-8
S.compareStrings(a, b, treatNumberAsString) / S.localizedStringSort(arr)
```

### LrColor (valeurs 0.0…1.0)
```lua
local LrColor = import 'LrColor'
LrColor(r, g, b, a) / LrColor(r,g,b) / LrColor(gray) / LrColor('red')
-- noms : black,white,gray,light gray,dark gray,red,green,blue,cyan,yellow,magenta,orange,purple,brown
-- accès : c:red() c:green() c:blue() c:alpha()
```

### Hash (utile pour intégrité / IDs)
```lua
import('LrMD5').digest(s)              -- MD5 hex
local LrDigest = import 'LrDigest'     -- SHA1/SHA256… (voir LrDigest.html)
```

---

## 20. LrShell & processus externes

```lua
local LrShell = import 'LrShell'
LrShell.revealInShell(path)                       -- ouvre l'Explorateur sur le fichier (1.3+)
LrShell.openFilesInApp({ file1, file2 }, appPath) -- ouvre dans une app (1.3+)
LrShell.openPathsViaCommandLine(files, appPath, extraArgs)  -- → exit code (3.0+)
```

> ⚠️ Les méthodes du projet précédent (`openPathInFileBrowser`, `openURL`) **n'existent pas**.
> Noms réels : `revealInShell`, `openFilesInApp`, `openPathsViaCommandLine`.
> Pour ouvrir une URL : `LrHttp.openUrlInBrowser(url)`.

Lancer le serveur Python / un process et récupérer stdout :

```lua
-- Recommandé : ne bloque que la tâche
local exitCode = import('LrTasks').execute('python "C:\\app\\main.py"')

-- io.popen (Lua 5.1) reste disponible mais bloque la tâche le temps de la lecture :
local h = io.popen('cmd /c python "C:\\chemin avec espaces\\script.py"')
local out = h:read('*all'); h:close()
```

---

## 21. LrLogger — débogage

```lua
local LrLogger = import 'LrLogger'
local log = LrLogger('LrAutomation')      -- crée ou retrouve un logger nommé
log:enable('logfile')                     -- 'print' | 'logfile' | 'traceback' | fonction | table
log:trace(...) log:debug(...) log:info(...) log:warn(...) log:error(...) log:fatal(...)
log:tracef('x=%d', 42)                    -- variantes *f (string.format) (2.0+)
local info = log:quickf('info')           -- version optimisée pour boucles serrées
```

Emplacement des fichiers de log (`'logfile'`) :
- **Windows** : `%LOCALAPPDATA%\Adobe\Lightroom\Logs\LrClassicLogs`
- macOS : `~/Library/Logs/Adobe/Lightroom/LrClassicLogs`

> `print(...)` reste visible dans la **Console Lua** intégrée. Outils externes possibles :
> DebugView (Windows), Console (macOS).

---

## 22. LrPrefs / LrPlugin / LrErrors

### LrPrefs — préférences persistantes du plugin
```lua
local prefs = import('LrPrefs').prefsForPlugin()   -- _PLUGIN par défaut (3.0+)
prefs.serverPort = 5000
local port = prefs.serverPort
-- Mutation profonde non détectée : réassigner pour sauver
prefs.t = prefs.t          -- force la sauvegarde après prefs.t[k]=v
-- Itération : prefs:pairs() (pairs() standard NE marche PAS)
```

### LrPlugin — objet `_PLUGIN` (global)
```lua
_PLUGIN.id        -- identifiant unique (= LrToolkitIdentifier)
_PLUGIN.path      -- chemin absolu du dossier .lrplugin
_PLUGIN.enabled   -- bool
_PLUGIN:hasResource(name) / :resourceId(name)
```

### LrErrors
```lua
local LrErrors = import 'LrErrors'
LrErrors.throwUserError('Message visible')
LrErrors.throwCanceled()
LrErrors.isCanceledError(errString)   -- depuis le message d'un pcall
```

---

## 23. Info.lua — manifeste plugin

Clés (SDK Guide chap. 2). Le dossier plugin **doit** se terminer par `.lrplugin`.

### Clés d'identité / cycle de vie
| Clé | Type | Rôle |
|---|---|---|
| `LrSdkVersion` | number (requis) | Version SDK préférée (ex. `15.2`) |
| `LrSdkMinimumVersion` | number | Version SDK minimale (ex. `12.0`) |
| `LrToolkitIdentifier` | string (requis) | ID unique style `com.domaine.lr-automation` |
| `LrPluginName` | string (requis ≥2.0) | Nom affiché (Plug-in Manager) |
| `VERSION` | table | `{ major=, minor=, revision=, build= , display= }` |
| `LrPluginInfoUrl` | string | URL d'info |
| `LrPluginInfoProvider` | string | Script pour la section du Plug-in Manager |
| `LrInitPlugin` | string | Script exécuté au chargement/rechargement |
| `LrForceInitPlugin` | bool (4.0+) | Force l'init au démarrage si le plugin a ≥1 menu |
| `LrShutdownPlugin` | string (3.0+) | Script au déchargement |
| `LrShutdownApp` | string (4.0+) | Script à la fermeture de Lr |
| `LrEnablePlugin` / `LrDisablePlugin` | string (3.0+) | Scripts activation/désactivation |

### Menus — sous-menu « Modules externes supplémentaires » (Plug-in Extras)
| Clé | Emplacement dans Lr |
|---|---|
| `LrLibraryMenuItems` | **Bibliothèque > Modules externes supplémentaires** |
| `LrExportMenuItems` | **Fichier > Modules externes supplémentaires** (sous la section Exporter) |
| `LrHelpMenuItems` | **Aide > Modules externes supplémentaires** |

> ⚠️ `LrFileMenuItems` **n'existe pas** : pour le menu Fichier, c'est `LrExportMenuItems`.
> Chaque entrée est une table (ou table de tables) `{ title=, file=, enabledWhen= }`.

`enabledWhen` (valeurs officielles) :
| Valeur | Activé quand |
|---|---|
| `'photosAvailable'` | des photos/vidéos sont présentes dans la grille |
| `'photosSelected'` | des photos sont sélectionnées (ignore vidéos) |
| `'videosSelected'` | des vidéos sont sélectionnées (ignore photos) |
| `'anythingSelected'` | photos **ou** vidéos sélectionnées |
| _(absent)_ | toujours activé |

> Si la sélection est très grande (>5000), les items sont activés quel que soit `enabledWhen`.

### Exemple Info.lua minimal pour ce projet
```lua
return {
    LrSdkVersion        = 15.2,
    LrSdkMinimumVersion = 12.0,

    LrToolkitIdentifier = 'com.valentin.lr-automation',
    LrPluginName        = 'Lr Automation',

    LrLibraryMenuItems = {
        { title = 'Lr Automation', file = 'Menu.lua', enabledWhen = 'photosAvailable' },
    },

    LrInitPlugin      = 'Init.lua',   -- démarrage de la boucle de polling
    LrForceInitPlugin = true,

    VERSION = { major = 0, minor = 1, revision = 0 },
}
```

> Autres clés (export/publish/métadonnées) si besoin un jour : `LrExportServiceProvider`,
> `LrExportFilterProvider`, `LrMetadataProvider`, `LrMetadataTagsetFactory`, `LrHttpHandler`.

---

## 24. Patterns du projet

### Extraire les données de la sélection (path + EXIF + develop)
```lua
local function getSelectedPhotosData()
    local catalog = import('LrApplication').activeCatalog()
    local photos  = catalog:getTargetPhotos()
    local data = {}
    for _, photo in ipairs(photos) do
        data[#data+1] = {
            photo_id = photo:getRawMetadata('uuid'),
            path     = photo:getRawMetadata('path'),
            exif = {
                iso      = photo:getRawMetadata('isoSpeedRating'),
                aperture = photo:getRawMetadata('aperture'),
                shutter  = photo:getRawMetadata('shutterSpeed'),
                focal    = photo:getRawMetadata('focalLength'),
                camera   = photo:getFormattedMetadata('cameraModel'),  -- via Formatted
            },
            current_develop = photo:getDevelopSettings(),               -- dans une task
        }
    end
    return data
end
-- ⚠️ Appeler depuis LrTasks.startAsyncTask (getRawMetadata/getDevelopSettings exigent une task).
```

### Appliquer des ajustements en batch
```lua
local function applyAdjustmentsBatch(adjustmentsByUuid)
    local catalog = import('LrApplication').activeCatalog()
    local photos  = catalog:getTargetPhotos()
    catalog:withWriteAccessDo('Lr Automation — Apply', function()
        for _, photo in ipairs(photos) do
            local adj = adjustmentsByUuid[photo:getRawMetadata('uuid')]
            if adj then photo:applyDevelopSettings(adj, 'Lr Automation') end
        end
    end)
end
```

### Boucle de polling HTTP (GET pending, POST result)
```lua
local function startPollingLoop()
    local LrTasks, LrHttp = import 'LrTasks', import 'LrHttp'
    local LrFunctionContext = import 'LrFunctionContext'

    LrTasks.startAsyncTask(function()
        -- Healthcheck (l'App doit être lancée)
        local ready = false
        for _ = 1, 10 do
            local b = LrHttp.get('http://localhost:5000/health', {})
            if b then ready = true break end
            LrTasks.sleep(0.5)
        end
        if not ready then
            import('LrDialogs').message('Lr Automation', 'App non accessible (app/main.py).', 'warning')
            return
        end

        while true do
            local body, hdrs = LrHttp.get('http://localhost:5000/jobs/pending', {})
            if body and body ~= '' and body ~= 'null' then
                local job = require('dkjson').decode(body)
                if job then
                    local result = handleJob(job)               -- exécute la requête SDK
                    -- POST du résultat : DOIT être dans postAsyncTaskWithContext
                    LrFunctionContext.postAsyncTaskWithContext('post', function()
                        LrHttp.post('http://localhost:5000/jobs/'..job.job_id..'/result',
                            require('dkjson').encode(result),
                            { { field='Content-Type', value='application/json' } })
                    end)
                end
            end
            LrTasks.sleep(0.3)
        end
    end)
end
```

### JSON & modules locaux
```lua
local json = require 'dkjson'                 -- lib embarquée à la racine du plugin
local t = json.decode(body)
local s = json.encode(t)

local PhotoData = require 'lib.PhotoData'     -- vos modules locaux
```

---

## 25. Limitations & contraintes

### Accessible via SDK (mise à jour vs anciennes notes)
| Fonctionnalité | Statut SDK |
|---|---|
| Develop settings batch (`applyDevelopSettings`) | ✅ sur toute photo, sans module Develop |
| Lire les develop settings (`getDevelopSettings`) | ✅ (dans une task) |
| Masques brush/gradient/radial/range/IA (créer) | ✅ `LrDevelopController` 11.0+ (module Develop) |
| Denoise IA / Raw Details / Super Resolution | ✅ `toggleEnhance` 14.5+ (module Develop) |
| Remove / Reflection / Distracting people | ✅ 14.1–14.5 (module Develop) |
| Point Color | ✅ 13.2+ (Process Version 3+) |
| Auto Tone / Auto WB | ✅ `setAutoTone` / `setAutoWhiteBalance` |

### Toujours NON accessible
| Fonctionnalité | Statut |
|---|---|
| Lire la géométrie/pixels d'un masque existant | ❌ |
| Generative Remove / Generative AI (résultat pixel) | ❌ (déclenchable, pas lisible) |
| Lens Blur — rendu | ❌ (params seulement) |
| Merge HDR / Panorama | ❌ UI uniquement (events haptiques exposés) |
| Histogramme rendu par Lr | ❌ (décoder le RAW côté App Python) |

### Contraintes Lua 5.1 (environnement Lr)
```lua
-- Absents en 5.1 :
--   //  (division entière)  → math.floor(a / b)
--   goto
--   table.pack / table.unpack → unpack()
--   bibliothèque utf8         → utiliser LrStringUtils
--   \d dans les patterns      → utiliser [0-9]
local n = math.floor(10 / 3)         -- 3
local a, b = unpack({ 1, 2 })
```

### Règles d'or
- **Tout I/O bloquant** (HTTP, sleep, gros fichiers) dans `LrTasks.startAsyncTask`.
- **Toute écriture** catalogue/develop dans `catalog:withWriteAccessDo` (ne pas imbriquer).
- `LrHttp.post` → envelopper dans `LrFunctionContext.postAsyncTaskWithContext`.
- **Chemins Windows** : passer par `LrPathUtils` (jamais de concaténation `/` ou `\` manuelle).
- **Écriture de fichier** : `io.open(path,'w')` (pas de `LrFileUtils.writeFile`).
- **JSON** : pas de lib native — embarquer `dkjson.lua` et `require 'dkjson'`.
- `LrDevelopController` agit sur la **photo active du module Développement** ; pour le batch,
  utiliser `photo:applyDevelopSettings()`.
