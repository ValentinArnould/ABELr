# Lightroom Classic — SDK API Reference (Lua)

> **Source**: official **Adobe Lightroom Classic 15.2** SDK (build 202602111402-ec4112e8).
> Rebuilt from `documentation/Lr_SDK_API/`:
> `API Reference/modules/*.html`, `Manual/Lightroom Classic SDK Guide.pdf`, `Sample Plugins/`.
>
> All signatures, enumerated values, and "First supported in version X" notes in this file
> are **verified** against the Adobe doc unless explicitly marked `⚠️`. A method's introduction
> version is noted when it is > 6.0 (useful since the project target is Lr 12+ = SDK 12+).
>
> - Language: **Lua 5.1**. SDK import: `import 'LrXxx'` (never `require` for SDK modules).
> - `require` remains valid for **your own** local modules (`require 'lib.Foo'`) and bundled libs (`require 'dkjson'`).

---

## Table of contents

1. [Available SDK imports](#1-available-sdk-imports)
2. [Platform & version detection](#2-platform--version-detection)
3. [LrApplication](#3-lrapplication)
4. [LrCatalog](#4-lrcatalog)
5. [LrPhoto — metadata](#5-lrphoto--metadata)
6. [LrPhoto — develop settings](#6-lrphoto--develop-settings)
7. [LrDevelopController](#7-lrdevelopcontroller)
8. [Develop parameters — SDK names](#8-develop-parameters--sdk-names)
9. [Masks & Denoise/Enhance via SDK](#9-masks--denoiseenhance-via-sdk)
10. [LrSelection / LrApplicationView](#10-lrselection--lrapplicationview)
11. [LrTasks — asynchrony](#11-lrtasks--asynchrony)
12. [LrFunctionContext](#12-lrfunctioncontext)
13. [LrHttp](#13-lrhttp)
14. [LrSocket](#14-lrsocket)
15. [LrDialogs](#15-lrdialogs)
16. [LrProgressScope](#16-lrprogressscope)
17. [LrView — custom dialogs](#17-lrview--custom-dialogs)
18. [LrFileUtils / LrPathUtils](#18-lrfileutils--lrpathutils)
19. [LrStringUtils / LrColor / LrDigest / LrMD5](#19-lrstringutils--lrcolor--lrdigest--lrmd5)
20. [LrShell & external processes](#20-lrshell--external-processes)
21. [LrLogger — debugging](#21-lrlogger--debugging)
22. [LrPrefs / LrPlugin / LrErrors](#22-lrprefs--lrplugin--lrerrors)
23. [Info.lua — plugin manifest](#23-infolua--plugin-manifest)
24. [Project patterns](#24-project-patterns)
25. [Limitations & constraints](#25-limitations--constraints)

---

## 1. Available SDK imports

All modules present in `API Reference/modules/` (SDK 15.2):

```lua
-- Core catalog / photo
local LrApplication       = import 'LrApplication'
local LrApplicationView   = import 'LrApplicationView'
local LrCatalog           -- no direct import: via LrApplication.activeCatalog()
local LrPhoto             -- class, not an importable namespace
local LrSelection         = import 'LrSelection'

-- Develop
local LrDevelopController  = import 'LrDevelopController'
local LrDevelopPreset      = import 'LrDevelopPreset'
local LrDevelopPresetFolder= import 'LrDevelopPresetFolder'

-- Tasks / context / errors
local LrTasks             = import 'LrTasks'
local LrFunctionContext   = import 'LrFunctionContext'
local LrErrors            = import 'LrErrors'
local LrRecursionGuard    = import 'LrRecursionGuard'

-- Network / IPC
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

-- Files / paths / strings / dates
local LrFileUtils         = import 'LrFileUtils'
local LrPathUtils         = import 'LrPathUtils'
local LrStringUtils       = import 'LrStringUtils'
local LrDate              = import 'LrDate'
local LrMath              = import 'LrMath'
local LrXml               = import 'LrXml'

-- System / plugin / prefs / security
local LrSystemInfo        = import 'LrSystemInfo'
local LrPrefs             = import 'LrPrefs'
local LrPasswords         = import 'LrPasswords'
local LrShell             = import 'LrShell'
local LrLogger            = import 'LrLogger'
local LrDigest            = import 'LrDigest'
local LrMD5               = import 'LrMD5'
local LrLocalization      = import 'LrLocalization'

-- Collections / keywords / folders
local LrCollection        -- classes obtained via the catalog
local LrCollectionSet
local LrKeyword
local LrFolder

-- Export / publish (not required for this project)
local LrExportSession     = import 'LrExportSession'
local LrExportSettings    = import 'LrExportSettings'
```

> `LrCatalog`, `LrPhoto`, `LrCollection`, `LrKeyword`, `LrFolder`, etc. are **classes**:
> instances are obtained from the catalog, the namespace itself is not imported.

---

## 2. Platform & version detection

`LrApplication.platform()` **does not exist**. To detect the OS:

```lua
-- Boolean globals defined by Lr (confirmed in SDK Guide)
if WIN_ENV then  -- Windows
elseif MAC_ENV then  -- macOS
end

-- LrSystemInfo (SDK 3.0+) for more details
local LrSystemInfo = import 'LrSystemInfo'
-- (see LrSystemInfo.html: architecture, memory, etc.)

-- Lr version
local LrApplication = import 'LrApplication'
local v = LrApplication.versionString()    -- e.g. "15.2"
local t = LrApplication.versionTable()
-- t.major, t.minor, t.revision, t.build_version (string), t.build (deprecated)
```

---

## 3. LrApplication

Namespace, functions called directly. **No simple `platform`/`cameraRawVersion`/`quit` method** ;
the app is closed via `LrApplication.shutdown()` (SDK 14.3+).

```lua
local LrApplication = import 'LrApplication'

-- Active catalog (SDK 1.3+)
local catalog = LrApplication.activeCatalog()        -- → LrCatalog

-- Version
LrApplication.versionString()                        -- "15.2"
LrApplication.versionTable()                         -- table {major,minor,revision,build_version,...}

-- Develop presets (useful for applying an existing look)
LrApplication.developPresetFolders()                 -- array LrDevelopPresetFolder (3.0+)
LrApplication.developPresetByUuid(uuid)              -- LrDevelopPreset (3.0+)
LrApplication.addDevelopPresetForPlugin(_PLUGIN, name, settingsTable)  -- 3.0+
LrApplication.getDevelopPresetsForPlugin(_PLUGIN, uuid)               -- 3.0+

-- Miscellaneous presets (table {name = uuid})
LrApplication.metadataPresets()                      -- 3.0+
LrApplication.filenamePresets()                      -- 3.0+
LrApplication.viewFilterPresets()                    -- 3.0+

-- Machine / license identifiers (plugin registration)
LrApplication.serialNumberHash()                     -- 3.0+
LrApplication.macAddressHash()                       -- 4.1+
LrApplication.purchaseSource()                       -- 'retail' | 'MAS' | 'CC'

-- Miscellaneous
LrApplication.backupAtNextShutdown(_PLUGIN.id)       -- 4.0+
LrApplication.shutdown()                             -- 14.3+ (quits Lr)
```

---

## 4. LrCatalog

Obtained via `LrApplication.activeCatalog()`. Most reads (getAllPhotos, find*, getKeywords…)
**must run inside an** `LrTasks` **task**. Writes **must** be inside `withWriteAccessDo`.

### Photo selection / access

```lua
local catalog = LrApplication.activeCatalog()

catalog:getTargetPhotos()    -- array LrPhoto : selection, otherwise the whole filmstrip (3.0+)
catalog:getTargetPhoto()     -- active LrPhoto (the most selected) or nil (3.0+)
catalog:getMultipleSelectedOrAllPhotos()  -- selection if >1, otherwise all visible ones (3.0+)
catalog:getAllPhotos()       -- array of ALL photos in the catalog (3.0+, inside a task)

catalog:setSelectedPhotos(activePhoto, { otherPhotos })  -- 3.0+
```

### Search

```lua
catalog:findPhotoByPath(absolutePath, caseSensitivity)   -- LrPhoto or nil (2.0+, inside a task)
catalog:findPhotoByUuid(uuid)                             -- LrPhoto or nil (2.0+, inside a task)
catalog:findPhotos{ sort=, ascending=, searchDesc={ criteria=, operation=, value= } }  -- 2.0+
-- searchDesc supports combine = "union"|"intersect"|"exclude" + nested criteria.
-- useful criteria: "rating","pick","labelColor","fileFormat","camera","isoSpeedRating",
--   "captureTime","hasAdjustments","cropped","aspectRatio","keywords","folder","collection"…
-- fileFormat enum : "DNG","RAW","JPG","TIFF","PNG","PSD","VIDEO","PSB","AVIF","JXL"
```

### Batch reading (efficient for 500-1000 photos)

```lua
catalog:batchGetRawMetadata(photos, keys)        -- table { [photo] = {key=val,...} } (3.0+)
catalog:batchGetFormattedMetadata(photos, keys)  -- same, formatted values (3.0+)
-- keys = nil → all available fields.
```

### Write transactions

```lua
-- Standard write (enters the Undo stack). DO NOT nest.
catalog:withWriteAccessDo('Undo action name', function(context)
    -- catalog / develop settings changes here
end, timeoutParams)   -- timeoutParams optional {timeout=, callback=, asynchronous=}

-- Write plugin-only metadata, outside the Undo stack
catalog:withPrivateWriteAccessDo(function(context) ... end, timeoutParams)

-- Long write with warning dialog + progress (large batch)
catalog:withProlongedWriteAccessDo{
    title = 'ABELr', pluginName = 'ABELr',
    func = function(context, progressScope) ... end,
}

-- Properties (read-only) to check the context
catalog.hasWriteAccess          -- bool
catalog.hasPrivateWriteAccess   -- bool
catalog:getPath()               -- absolute path of the .lrcat (3.0+)
```

> **Important (3.0+)**: objects created inside a `withWriteAccessDo` (collections, etc.) are only
> accessible **after** the callback finishes. Nested `with___AccessDo` calls fail.
> Several consecutive `withWriteAccessDo` calls with no user interaction in between are merged into a single Undo step.

### Other (collections, keywords, import)

```lua
catalog:createCollection(name, parentSet, canReturnPrior)       -- 3.0+ (inside writeAccess)
catalog:createCollectionSet(name, parentSet, canReturnPrior)    -- 3.0+
catalog:createSmartCollection(name, searchDesc, parent, canReturnPrior)
catalog:getChildCollections() / :getChildCollectionSets()       -- (inside a task)
catalog:createKeyword(name, synonyms, includeOnExport, parent, returnExisting)
catalog:getKeywords()                                           -- (inside a task)
catalog:getFolders() / :getFolderByPath(path)
catalog:addPhoto(path, stackWith, position, metaPresetUUID, developPresetUUID)  -- 2.0+ (12.5 for presets)
catalog:buildSmartPreviews(photos)                             -- 5.0+ (inside a task)
catalog:setActiveSources(sources) / :getActiveSources()
catalog:updateAISettings(photos)                              -- 13.3+ (inside writeAccess)
catalog:deleteAllEmptyMasks(photos)                          -- 14.0+ (inside writeAccess)
```

---

## 5. LrPhoto — metadata

Instances obtained from `catalog:getTargetPhotos()` etc. `getRawMetadata`/`getFormattedMetadata` reads
**must run inside an** `LrTasks` **task** (since 3.0, write-access is no longer needed to read).

### `photo:getRawMetadata(key)` — raw (typed) values

```lua
-- File / identity
photo:getRawMetadata('path')          -- string: current absolute path (or last known) (3.0+)
photo:getRawMetadata('uuid')          -- string: persistent ID (3.0+)
photo.localIdentifier                  -- number: local catalog ID (property, 4.0+)
photo:getRawMetadata('fileSize')      -- number (bytes)
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
photo:getRawMetadata('isoSpeedRating')    -- number (e.g. 200)
photo:getRawMetadata('aperture')          -- number: f-number denominator (e.g. 2.8)
photo:getRawMetadata('shutterSpeed')      -- number: seconds (1/60 = 0.01666)
photo:getRawMetadata('focalLength')       -- number: mm
photo:getRawMetadata('focalLength35mm')   -- number: 35mm-equivalent mm
photo:getRawMetadata('exposureBias')      -- number (e.g. -0.6666)
photo:getRawMetadata('flash')             -- bool or nil
photo:getRawMetadata('dateTimeOriginalISO8601')  -- string ISO 8601 (2.0+, reliable)
photo:getRawMetadata('gps')               -- { latitude=, longitude= } or nil
photo:getRawMetadata('gpsAltitude')       -- number (m)

-- Rating / flags
photo:getRawMetadata('rating')            -- number 0-5 or nil
photo:getRawMetadata('pickStatus')        -- 1 pick / 0 neutral / -1 reject (4.0+)
photo:getRawMetadata('colorNameForLabel') -- 'red','yellow','green','blue','purple','none'

-- Virtual copies / stacks
photo:getRawMetadata('isVirtualCopy')     -- bool
photo:getRawMetadata('countVirtualCopies')-- number
photo:getRawMetadata('masterPhoto')       -- LrPhoto (if virtual copy)

-- Smart preview (useful if the RAW is offline)
photo:getRawMetadata('smartPreviewInfo')  -- { smartPreviewPath=, smartPreviewSize= } (5.0+)

-- Keywords / custom
photo:getRawMetadata('keywords')          -- array LrKeyword (3.0+)
photo:getRawMetadata('customMetadata')    -- table (3.0+)
photo:getRawMetadata('isExported')        -- bool (13.3+)
```

> ⚠️ The **camera** name is NOT in `getRawMetadata`. The model comes from
> `getFormattedMetadata('cameraModel')` / `('cameraMake')`. Same for `lens`.

### `photo:getFormattedMetadata(key)` — displayable strings (do not parse)

```lua
photo:getFormattedMetadata('cameraModel')   -- e.g. "ILCE-7M4"
photo:getFormattedMetadata('cameraMake')    -- e.g. "SONY"
photo:getFormattedMetadata('lens')          -- e.g. "FE 85mm F1.8"
photo:getFormattedMetadata('fileName')      -- "DSC00123.ARW"
photo:getFormattedMetadata('fileType')      -- "Raw" / "DNG" / …
photo:getFormattedMetadata('exposure')      -- "1/200 sec at f/2.8"
photo:getFormattedMetadata('isoSpeedRating')-- "ISO 800"
photo:getFormattedMetadata('focalLength')   -- "85 mm"
photo:getFormattedMetadata('title') / ('caption') / ('label')
photo:getFormattedMetadata('croppedDimensions')  -- "3072 x 2304"
-- key = nil → table of all fields.
```

### `photo:setRawMetadata(key, value)` — inside `withWriteAccessDo`

```lua
catalog:withWriteAccessDo('Set metadata', function()
    photo:setRawMetadata('rating', 5)            -- number
    photo:setRawMetadata('label', 'red')         -- color label name
    photo:setRawMetadata('colorNameForLabel', 'red')
    photo:setRawMetadata('pickStatus', 1)        -- 1 / 0 / -1 (4.0+)
    photo:setRawMetadata('title', 'Title')
    photo:setRawMetadata('caption', 'Caption')
    photo:setRawMetadata('gps', { latitude=35.1, longitude=86.7 })  -- 4.0+
end)
```

> EXIF (ISO, shutter speed, aperture, focal length, model…) is **read-only** — `setRawMetadata`
> only accepts rating/label/pick/gps/title/caption and IPTC fields (see LrPhoto.html).

### Other useful LrPhoto methods

```lua
photo:getDevelopSettings()                   -- full table (3.0+, inside a task) — see §6
photo:applyDevelopSettings(settings, optHistoryName, optFlattenAutoNow)  -- 6.0+, writeAccess
photo:applyDevelopPreset(preset, _PLUGIN, presetAmount, updateAISettings)-- 3.0+, writeAccess
photo:applyDevelopSnapshot(id) / :createDevelopSnapshot(name, updateInPlace) / :getDevelopSnapshots()
photo:requestJpegThumbnail(w, h, function(jpeg, err) ... end)  -- 5.0+, inside a task
photo:checkPhotoAvailability()               -- bool: is the file present? (2.0+, inside a task)
photo:buildSmartPreview() / :deleteSmartPreview()              -- 5.0+
photo:addKeyword(kw) / :removeKeyword(kw)    -- writeAccess
photo:getPropertyForPlugin(_PLUGIN, fieldId, optVersion, noThrow)  -- custom metadata
photo:setPropertyForPlugin(_PLUGIN, fieldId, value)               -- writeAccess
photo:type()                                 -- 'LrPhoto'
photo.catalog                                -- parent LrCatalog
```

---

## 6. LrPhoto — develop settings

`photo:getDevelopSettings()` (3.0+, **inside a task**) returns a large table.
⚠️ Adobe notes: *« The develop settings APIs are considered experimental »* — do not rely on a
missing key ; the reference list remains the UI. Confirmed members (excerpt):

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

Apply in batch (no need for the Develop module):

```lua
catalog:withWriteAccessDo('Apply adjustments', function()
    photo:applyDevelopSettings({
        Exposure    = 0.35,   -- see §8 for ranges
        Temperature = 5600,
        Tint        = -5,
        Highlights  = -20,
        Shadows     = 15,
    }, 'ABELr')       -- 2nd arg = history step name (optional)
end)
```

> `applyDevelopSettings` works on any photo (no need for the Develop module to be active),
> unlike `LrDevelopController` (§7). This is **the** way to do batch processing.

---

## 7. LrDevelopController

Namespace. Operates on the **photo currently active in the Develop module only**: most
functions require *« Must be called while the Develop module is active »*. For batch work, prefer
`photo:applyDevelopSettings()` (§6). Useful here mainly for `getRange`, auto-tone, and Enhance/Denoise.

```lua
local LrDevelopController = import 'LrDevelopController'

-- Read / write a parameter (6.0+)
local val = LrDevelopController.getValue('Exposure')
LrDevelopController.setValue('Exposure', 0.5, withClippingOn)   -- 3rd arg optional (clipping overlay)
LrDevelopController.increment('Exposure') / .decrement('Exposure')
LrDevelopController.resetToDefault('Exposure')
LrDevelopController.resetAllDevelopAdjustments()

-- Actual range of a parameter (at runtime) — valuable since the SDK does not document the bounds
local mn, mx = LrDevelopController.getRange('Exposure')         -- 6.0+

-- Auto
LrDevelopController.setAutoTone()           -- 7.4+
LrDevelopController.setAutoWhiteBalance()    -- 7.4+

-- Process version
LrDevelopController.getProcessVersion()
LrDevelopController.setProcessVersion('Version 6')  -- "Version 1".."Version 6"

-- Tools / panels
LrDevelopController.selectTool('crop')       -- "loupe","crop","dust","redeye","masking","upright",
                                             --  "point_color","local_point_color","depth_refinement"
LrDevelopController.getSelectedTool()
LrDevelopController.revealPanel('adjustPanel')        -- expands a panel
LrDevelopController.revealPanelIfVisible('tonePanel')

-- Behavior settings (avoid too many history states in a loop)
LrDevelopController.setTrackingDelay(seconds)
LrDevelopController.setMultipleAdjustmentThreshold(seconds)   -- default 0.5 s
LrDevelopController.startTracking('Exposure') / .stopTracking()

-- Observe changes (UI)
LrDevelopController.addAdjustmentChangeObserver(context, observer, function(obs) ... end)  -- 6.0+
```

> ⚠️ `Temperature` is **logarithmic** for RAW/DNG via `setValue` (everything else is linear).
> `Texture` is disabled in Process Version 1 & 2.

Valid panels for `revealPanel` / parameter group names:
`adjustPanel, tonePanel, mixerPanel, colorGradingPanel, detailPanel, lensCorrectionsPanel,
effectsPanel, calibratePanel, lensBlurPanel`.

---

## 8. Develop parameters — SDK names

Names usable in `photo:applyDevelopSettings({})` and `LrDevelopController.setValue()`.
**Names are confirmed** (from the `LrDevelopController` list + `getDevelopSettings` table). The **ranges**
below are the usual Camera Raw UI ranges (the SDK does not fix them — query
`LrDevelopController.getRange(param)` at runtime for the exact bounds).

### Exposure & tone
| Param | UI range | Note |
|---|---|---|
| `Exposure` | −5.0 … +5.0 | stops |
| `Contrast` | −100 … +100 | |
| `Highlights` `Shadows` `Whites` `Blacks` | −100 … +100 | |
| `Clarity` `Dehaze` `Texture` | −100 … +100 | |
| `Brightness` | −150 … +150 | Process Version 1/2 only |

### White balance
| Param | Value | Note |
|---|---|---|
| `Temperature` | 2000 … 50000 (K) | logarithmic for RAW |
| `Tint` | −150 … +150 | |
| `WhiteBalance` | string | `'As Shot'`,`'Auto'`,`'Custom'`,`'Daylight'`,`'Cloudy'`,`'Shade'`,`'Tungsten'`,`'Fluorescent'`,`'Flash'` |

### Global color
`Vibrance`, `Saturation`: −100 … +100.

### HSL (8 channels: Red, Orange, Yellow, Green, Aqua, Blue, Purple, Magenta)
| SDK prefix | Range |
|---|---|
| `HueAdjustment<Channel>` | −100 … +100 |
| `SaturationAdjustment<Channel>` | −100 … +100 |
| `LuminanceAdjustment<Channel>` | −100 … +100 |
| `GrayMixer<Channel>` | −100 … +100 (B&W mix) |

### Color Grading (Process Version 3+)
> ⚠️ Hybrid: **shadows** and **highlights** use the `SplitToning*` names for Hue/Sat,
> but `ColorGrade*Lum` for luminance. Midtones & global use `ColorGrade*`.

| Zone | Hue | Saturation | Luminance |
|---|---|---|---|
| Shadows | `SplitToningShadowHue` | `SplitToningShadowSaturation` | `ColorGradeShadowLum` |
| Highlights | `SplitToningHighlightHue` | `SplitToningHighlightSaturation` | `ColorGradeHighlightLum` |
| Midtones | `ColorGradeMidtoneHue` | `ColorGradeMidtoneSat` | `ColorGradeMidtoneLum` |
| Global | `ColorGradeGlobalHue` | `ColorGradeGlobalSat` | `ColorGradeGlobalLum` |

Ranges: Hue 0…360, Sat 0…100, Lum −100…+100. Plus: `SplitToningBalance` (−100…+100),
`ColorGradeBlending` (0…100).

### Parametric curve
`ParametricShadows`, `ParametricDarks`, `ParametricLights`, `ParametricHighlights`: −100…+100.
Split points: `ParametricShadowSplit`, `ParametricMidtoneSplit`, `ParametricHighlightSplit`.
Point curve: table `ToneCurvePV2012` (+ `…Red/Green/Blue`).

### Detail (sharpening / noise)
| Param | Range |
|---|---|
| `Sharpness` | 0 … 150 |
| `SharpenRadius` | 0.5 … 3.0 |
| `SharpenDetail` | 0 … 100 |
| `SharpenEdgeMasking` | 0 … 100 |
| `LuminanceSmoothing` | 0 … 100 |
| `LuminanceNoiseReductionDetail` / `…Contrast` | 0 … 100 |
| `ColorNoiseReduction` (+`Detail`, +`Smoothness`) | 0 … 100 |

> **AI Denoise**: not a parameter of `applyDevelopSettings`. See §9 (`LrDevelopController.toggleEnhance` /
> `changeDenoiseAmount`, requires the Develop module active).

### Optical corrections / geometry
| Param | Value |
|---|---|
| `LensProfileEnable` | 0 / 1 |
| `AutoLateralCA` | 0 / 1 |
| `VignetteAmount` / `VignetteMidpoint` | optical vignetting |
| `DefringePurpleAmount` / `DefringeGreenAmount` (+ HueLo/Hi) | color fringing |
| `PerspectiveVertical/Horizontal/Rotate/Scale/Aspect/X/Y` | manual transform |
| `PerspectiveUpright` | Upright mode |
| `straightenAngle` / `CropAngle` | straightening |

### Effects
`PostCropVignetteAmount/Midpoint/Feather/Roundness/Style/HighlightContrast`,
`GrainAmount` (0…100), `GrainSize` (0…100), `GrainFrequency` (0…100).

### Crop
`CropTop`, `CropLeft`, `CropBottom`, `CropRight`: proportions 0.0…1.0. `CropAngle`: −45…+45.

### Camera calibration
`CameraProfile` (string, e.g. `'Camera Standard'`, `'Adobe Standard'`), `ShadowTint`,
`RedHue`/`RedSaturation`, `GreenHue`/`GreenSaturation`, `BlueHue`/`BlueSaturation` (−100…+100),
`EnableCalibration` (bool).

### ProcessVersion
Valid strings: `"Version 1"` … `"Version 6"` (via `LrDevelopController.setProcessVersion`).

---

## 9. Masks & Denoise/Enhance via SDK

Unlike older versions, SDK 11+ exposes **masking** and SDK 14.5 exposes **Enhance**.
All of these functions require the **Develop module to be active** (and often the tool to be open).

### Masks (LrDevelopController, 11.0+)
```lua
LrDevelopController.goToMasking()
LrDevelopController.createNewMask(maskType, maskSubtype)
LrDevelopController.addToCurrentMask(maskType, maskSubtype)
LrDevelopController.subtractFromCurrentMask(...) / .intersectWithCurrentMask(...)
LrDevelopController.getAllMasks() / .getSelectedMask() / .selectMask(id)
LrDevelopController.invertMask(id) / .duplicateAndInvertMask(id) / .deleteMask(id)
```
- `maskType`: `"brush"`, `"gradient"`, `"radialGradient"`, `"rangeMask"`, `"aiSelection"`.
- `maskSubtype` (for rangeMask/aiSelection): `"color"`, `"luminance"`, `"depth"`, `"subject"`,
  `"sky"`, `"background"`, `"objects"`, `"people"`, `"landscape"`.

> AI masks (Subject, Sky, Background, People…) **can** be created via `createNewMask("aiSelection", subtype)`.
> But **reading** the geometry/pixels of a mask remains impossible.

### Enhance: AI Denoise / Raw Details / Super Resolution (14.5+)
```lua
LrDevelopController.toggleEnhance('denoise', denoiseAmount, callback, args)  -- 'denoise'|'rawDetails'|'superRes'
LrDevelopController.changeDenoiseAmount(amount)        -- 1..100
LrDevelopController.getEnhancePanelState()             -- table {denoiseAmount, denoiseEnabled,...}
```

### Remove / Reflection / Distracting people (14.1–14.5)
`goToRemove(spotType, whichFeature)`, `setRemovePanelPreferences{...}`, `getAllSpots`,
`toggleReflectionRemoval(amount, quality)`, `detectDistractingPeople()`, etc. (see LrDevelopController.html).

### Point Color (13.2+, Process Version 3+)
`addPointColorSwatch`, `selectPointColorSwatch(1..8)`, `updateSelectedPointColorSwatch`,
`getValue('PointColors')` / `getValue('local_PointColors')`.

---

## 10. LrSelection / LrApplicationView

### LrSelection (6.0+) — acts on the grid selection or the active photo
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

### LrApplicationView — view / module state
```lua
local LrApplicationView = import 'LrApplicationView'
LrApplicationView.getCurrentModuleName()    -- "library","develop","map","book","slideshow","print","web"
LrApplicationView.switchToModule('develop') -- required before using LrDevelopController
LrApplicationView.showView('grid')          -- "loupe","grid","compare","survey","develop_loupe",…
LrApplicationView.zoomIn() / .zoomOut() / .toggleZoom() / .zoomToOneToOne()
LrApplicationView.isSecondaryDisplayOn() / .showSecondaryView('loupe')
```

> To drive `LrDevelopController` on a specific photo: `catalog:setSelectedPhotos(photo,{})`
> then `LrApplicationView.switchToModule('develop')`.

---

## 11. LrTasks — asynchrony

Any blocking I/O (HTTP, sleep, large files) must run inside a cooperative task.
No real multithreading: these are coroutines on the main thread.

```lua
local LrTasks = import 'LrTasks'

LrTasks.startAsyncTask(function() ... end, 'optName')   -- 1.3+ ; shows an error dialog if it throws
LrTasks.startAsyncTaskWithoutErrorHandler(function() ... end)  -- without auto dialog
LrTasks.sleep(0.3)        -- seconds (float)
LrTasks.yield()           -- briefly yields control (call inside long loops)
LrTasks.canYield()        -- bool: can we yield here?
LrTasks.pcall(func, ...)  -- yield-safe pcall
LrTasks.execute(cmd)      -- like os.execute but only blocks the task → exit code (number)
```

> `LrTasks.execute` is the recommended way to launch an external process without freezing Lr
> (preferable to plain `io.popen`/`os.execute`). See §20.

---

## 12. LrFunctionContext

Cleans up resources at the end of a function/task. **Required** for `LrHttp.post`,
observable property tables (`LrBinding`), `LrProgressScope`, `LrSocket`.

```lua
local LrFunctionContext = import 'LrFunctionContext'

LrFunctionContext.callWithContext('name', function(context) ... end, ...)   -- 1.3+
LrFunctionContext.pcallWithContext('name', function(context) ... end)       -- protected variant
LrFunctionContext.postAsyncTaskWithContext('name', function(context) ... end) -- task + context

-- On the context object:
context:addCleanupHandler(function(success, ...) ... end)   -- called at the end (reverse order)
context:addFailureHandler(function(false, msg) ... end)     -- called only on error
context:addOperationTitleForError('Operation failed.')
```

---

## 13. LrHttp

**Only inside an asynchronous task.** On network error, the methods return `nil`
+ an info object containing `info.error.errorCode` (`"timedOut"`, `"cannotConnectToHost"`,
`"cannotFindHost"`, `"networkConnectionLost"`, `"cancelled"`, …).

```lua
local LrHttp = import 'LrHttp'

-- GET (1.3+) — usable inside any task
local body, headers = LrHttp.get(url, headersTable, timeout)
-- headersTable : { { field='X', value='Y' }, ... } ; timeout in seconds (optional)
-- headers.status = HTTP code (integer). headers = nil on network error.

-- POST (1.3+) — MUST be called from LrFunctionContext.postAsyncTaskWithContext()
local body, headers = LrHttp.post(url, postBody, headersTable, method, timeout, totalSize)
-- method optional (default "POST"). postBody : string (or a function supplying chunks, 4.1+).

-- Multipart POST (file upload)
local body, headers = LrHttp.postMultipart(url, content, headers, timeout, callbackFn, suppressFormData)
-- content : { { name=, filePath=, fileName=, contentType= }, { name=, value= } }

LrHttp.openUrlInBrowser(url)            -- opens in the browser
LrHttp.parseCookie(setCookieValue)      -- parses a Set-Cookie header
```

> **Content-Type**: if not specified, Lr adds `text/plain`. For JSON, pass
> `{ field='Content-Type', value='application/json' }`. To force its absence: value `'skip'`.
>
> ⚠️ Important detail: `LrHttp.post` requires the `postAsyncTaskWithContext` context. `LrHttp.get`
> works inside any `startAsyncTask`. For the polling loop (frequent GET +
> occasional POST of results), wrap the results submission in `postAsyncTaskWithContext`.

Check the status:

```lua
if not headers then
    -- App not started / network error
elseif headers.status == 200 then
    -- OK
end
```

---

## 14. LrSocket

Localhost sockets (6.0+) for bidirectional IPC. Not required for this project (LrHttp is enough),
but it's the alternative if you want the App to **push** to the plugin without polling.
Closed automatically if the plugin is disabled/removed.

```lua
local LrSocket = import 'LrSocket'
LrFunctionContext.callWithContext('sock', function(context)
    local sender = LrSocket.bind {
        functionContext = context,
        plugin = _PLUGIN,
        port = 0,             -- 0 = OS-chosen port
        mode = 'send',        -- 'send' | 'receive'
        onConnected = function(socket, port) end,
        onMessage   = function(socket, message) end,   -- 'receive' mode
        onClosed    = function(socket) end,
        onError     = function(socket, err) if err=='timeout' then socket:reconnect() end end,
    }
    sender:send('Hello')      -- 'send' mode
    sender:close()
end)
```

> Full examples in `Sample Plugins/remote_control_socket*.lrdevplugin/`.

---

## 15. LrDialogs

```lua
local LrDialogs = import 'LrDialogs'

LrDialogs.message(message, info, style)   -- style : "warning"(default) | "info" | "critical"
LrDialogs.showError(errorString)
LrDialogs.showBezel(message, fadeDelay)   -- brief toast (5.0+)

-- Confirmation → "ok" | "cancel" | "other"
local r = LrDialogs.confirm(message, info, actionVerb, cancelVerb, otherVerb)

-- Custom modal dialog (LrView) → button return value
local r = LrDialogs.presentModalDialog{
    title = '...', contents = viewHierarchy,
    actionVerb = 'OK', cancelVerb = 'Cancel',  -- cancelVerb = "  " (3 spaces) to hide Cancel
    otherVerb = nil, resizable = false,
}
LrDialogs.presentFloatingDialog(_PLUGIN, { title=, contents=, blockTask=, selectionChangeObserver= })

-- File pickers
local paths = LrDialogs.runOpenPanel{ title=, canChooseFiles=true, canChooseDirectories=false,
                                      allowsMultipleSelection=true, initialDirectory= }  -- array|nil
local path  = LrDialogs.runSavePanel{ title=, requiredFileType='json' }                 -- string|nil

-- Modal progress (blocking) → LrProgressScope
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
        title = 'ABELr — Analysis',
        functionContext = context,    -- auto-completed at the end of the context
        caption = 'Initializing…',
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

Methods: `setPortionComplete(done, total)`, `getPortionComplete()`, `setCaption(s)`,
`setIndeterminate()`, `isCanceled()`, `setCancelable(bool)`, `cancel()`, `pause()`/`isPaused()` (7.5+),
`done()`. Scopes can be nested via `parent=` / `parentEndRange=`.

---

## 17. LrView — custom dialogs

```lua
local LrView    = import 'LrView'
local LrBinding = import 'LrBinding'
local f = LrView.osFactory()

-- Observable property table (MUST be created inside a function context)
LrFunctionContext.callWithContext('dlg', function(context)
    local props = LrBinding.makePropertyTable(context)
    props.port = 5000

    local c = f:column {
        spacing = f:dialog_spacing(),
        f:row { f:static_text { title = 'Port:' },
                f:edit_field { value = LrView.bind('port'), width_in_chars = 6 } },
        f:checkbox  { title = 'Option', value = LrView.bind('flag') },
        f:separator { fill_horizontal = 1 },
        f:push_button { title = 'Action', action = function() ... end },
    }
    LrDialogs.presentModalDialog{ title = 'ABELr', contents = c }
end)
```

Common controls (see `LrView*.html`): `static_text`, `edit_field`, `checkbox`, `radio_button`,
`popup_menu`, `combo_box`, `slider`, `push_button`, `password_field`, `picture`, `catalog_photo`.
Containers: `row`, `column`, `group_box`, `scrolled_view`, `tab_view`, `view`. Binding via
`LrView.bind('key')` (two-way) on an observable property table.

---

## 18. LrFileUtils / LrPathUtils

### LrPathUtils — path manipulation (always via this module on Windows)
```lua
local LrPathUtils = import 'LrPathUtils'
LrPathUtils.child(path, child)            -- join  'C:\d' + 'f' → 'C:\d\f'
LrPathUtils.parent(path)                  -- parent folder (nil for root, 2.0+)
LrPathUtils.leafName(path)                -- last component
LrPathUtils.extension(path)               -- 'ARW' (without the dot), '' if none
LrPathUtils.removeExtension(path)
LrPathUtils.addExtension(path, ext) / .replaceExtension(path, ext)
LrPathUtils.isAbsolute(path) / .isRelative(path)
LrPathUtils.makeAbsolute(path, base) / .makeRelative(path, base)
LrPathUtils.standardizePath(path)         -- resolves .. and ~
LrPathUtils.getStandardFilePath(which)    -- 'home','temp','desktop','appPrefs','pictures','documents','appData'
LrPathUtils.maxPathLength()
```

### LrFileUtils — files/folders
```lua
local LrFileUtils = import 'LrFileUtils'
LrFileUtils.exists(path)            -- 'file' | 'directory' | false
LrFileUtils.readFile(path)          -- string (prefer over io for non-ASCII paths)
LrFileUtils.copy(src, dst) / .move(src, dst)      -- dst's parent folder must exist
LrFileUtils.delete(path)            -- immediate deletion (prefer moveToTrash)
LrFileUtils.moveToTrash(path)
LrFileUtils.createDirectory(path) / .createAllDirectories(path)   -- recursive
LrFileUtils.chooseUniqueFileName(path)
LrFileUtils.fileAttributes(path)    -- { fileSize, fileCreationDate, fileModificationDate }
LrFileUtils.isReadable/isWritable/isDeletable(path)
LrFileUtils.makeFileWritable(path)
-- Iterators (for ... do ; DO NOT break) :
for p in LrFileUtils.files(dir) do end
for p in LrFileUtils.directoryEntries(dir) do end
for p in LrFileUtils.recursiveFiles(dir) do end
for p in LrFileUtils.recursiveDirectoryEntries(dir) do end
```

> ⚠️ **There is NO `LrFileUtils.writeFile`.** To write a file, use Lua's standard
> `io` library:
> ```lua
> local fh = io.open(path, 'w'); fh:write(content); fh:close()
> ```

---

## 19. LrStringUtils / LrColor / LrDigest / LrMD5

### LrStringUtils (UTF-8)
```lua
local S = import 'LrStringUtils'
S.trimWhitespace(s)
S.lower(s) / S.upper(s)              -- locale-aware case (handles non-ASCII, unlike string.lower)
S.numberToString(n, precision) / S.numberToStringWithSeparators(n, precision)
S.byteString(n, precision)          -- "1.90 MB"
S.encodeBase64(s) / S.decodeBase64(s)
S.isOnlyAscii(s)
S.truncate(s, maxBytes)             -- truncates while preserving UTF-8
S.compareStrings(a, b, treatNumberAsString) / S.localizedStringSort(arr)
```

### LrColor (values 0.0…1.0)
```lua
local LrColor = import 'LrColor'
LrColor(r, g, b, a) / LrColor(r,g,b) / LrColor(gray) / LrColor('red')
-- names : black,white,gray,light gray,dark gray,red,green,blue,cyan,yellow,magenta,orange,purple,brown
-- access : c:red() c:green() c:blue() c:alpha()
```

### Hash (useful for integrity / IDs)
```lua
import('LrMD5').digest(s)              -- MD5 hex
local LrDigest = import 'LrDigest'     -- SHA1/SHA256… (see LrDigest.html)
```

---

## 20. LrShell & external processes

```lua
local LrShell = import 'LrShell'
LrShell.revealInShell(path)                       -- opens Explorer on the file (1.3+)
LrShell.openFilesInApp({ file1, file2 }, appPath) -- opens in an app (1.3+)
LrShell.openPathsViaCommandLine(files, appPath, extraArgs)  -- → exit code (3.0+)
```

> ⚠️ The previous project's methods (`openPathInFileBrowser`, `openURL`) **do not exist**.
> Real names: `revealInShell`, `openFilesInApp`, `openPathsViaCommandLine`.
> To open a URL: `LrHttp.openUrlInBrowser(url)`.

Launching the Python server / a process and capturing stdout:

```lua
-- Recommended: only blocks the task
local exitCode = import('LrTasks').execute('python "C:\\app\\main.py"')

-- io.popen (Lua 5.1) remains available but blocks the task while reading:
local h = io.popen('cmd /c python "C:\\path with spaces\\script.py"')
local out = h:read('*all'); h:close()
```

---

## 21. LrLogger — debugging

```lua
local LrLogger = import 'LrLogger'
local log = LrLogger('ABELr')      -- creates or retrieves a named logger
log:enable('logfile')                     -- 'print' | 'logfile' | 'traceback' | function | table
log:trace(...) log:debug(...) log:info(...) log:warn(...) log:error(...) log:fatal(...)
log:tracef('x=%d', 42)                    -- *f variants (string.format) (2.0+)
local info = log:quickf('info')           -- version optimized for tight loops
```

Log file location (`'logfile'`):
- **Windows**: `%LOCALAPPDATA%\Adobe\Lightroom\Logs\LrClassicLogs`
- macOS: `~/Library/Logs/Adobe/Lightroom/LrClassicLogs`

> `print(...)` remains visible in the built-in **Lua Console**. Possible external tools:
> DebugView (Windows), Console (macOS).

---

## 22. LrPrefs / LrPlugin / LrErrors

### LrPrefs — persistent plugin preferences
```lua
local prefs = import('LrPrefs').prefsForPlugin()   -- _PLUGIN by default (3.0+)
prefs.serverPort = 5000
local port = prefs.serverPort
-- Deep mutation not detected: reassign to save
prefs.t = prefs.t          -- forces the save after prefs.t[k]=v
-- Iteration: prefs:pairs() (standard pairs() does NOT work)
```

### LrPlugin — `_PLUGIN` object (global)
```lua
_PLUGIN.id        -- unique identifier (= LrToolkitIdentifier)
_PLUGIN.path      -- absolute path of the .lrplugin folder
_PLUGIN.enabled   -- bool
_PLUGIN:hasResource(name) / :resourceId(name)
```

### LrErrors
```lua
local LrErrors = import 'LrErrors'
LrErrors.throwUserError('Visible message')
LrErrors.throwCanceled()
LrErrors.isCanceledError(errString)   -- from a pcall's message
```

---

## 23. Info.lua — plugin manifest

Keys (SDK Guide chap. 2). The plugin folder **must** end with `.lrplugin`.

### Identity / lifecycle keys
| Key | Type | Role |
|---|---|---|
| `LrSdkVersion` | number (required) | Preferred SDK version (e.g. `15.2`) |
| `LrSdkMinimumVersion` | number | Minimum SDK version (e.g. `12.0`) |
| `LrToolkitIdentifier` | string (required) | Unique ID, style `com.domain.abelr` |
| `LrPluginName` | string (required ≥2.0) | Display name (Plug-in Manager) |
| `VERSION` | table | `{ major=, minor=, revision=, build= , display= }` |
| `LrPluginInfoUrl` | string | Info URL |
| `LrPluginInfoProvider` | string | Script for the Plug-in Manager section |
| `LrInitPlugin` | string | Script run on load/reload |
| `LrForceInitPlugin` | bool (4.0+) | Forces init at startup if the plugin has ≥1 menu |
| `LrShutdownPlugin` | string (3.0+) | Script on unload |
| `LrShutdownApp` | string (4.0+) | Script on Lr close |
| `LrEnablePlugin` / `LrDisablePlugin` | string (3.0+) | Enable/disable scripts |

### Menus — "Plug-in Extras" submenu
| Key | Location in Lr |
|---|---|
| `LrLibraryMenuItems` | **Library > Plug-in Extras** |
| `LrExportMenuItems` | **File > Plug-in Extras** (below the Export section) |
| `LrHelpMenuItems` | **Help > Plug-in Extras** |

> ⚠️ `LrFileMenuItems` **does not exist**: for the File menu, it's `LrExportMenuItems`.
> Each entry is a table (or table of tables) `{ title=, file=, enabledWhen= }`.

`enabledWhen` (official values):
| Value | Enabled when |
|---|---|
| `'photosAvailable'` | photos/videos are present in the grid |
| `'photosSelected'` | photos are selected (ignores videos) |
| `'videosSelected'` | videos are selected (ignores photos) |
| `'anythingSelected'` | photos **or** videos selected |
| _(absent)_ | always enabled |

> If the selection is very large (>5000), items are enabled regardless of `enabledWhen`.

### Minimal Info.lua example for this project
```lua
return {
    LrSdkVersion        = 15.2,
    LrSdkMinimumVersion = 12.0,

    LrToolkitIdentifier = 'com.abelr',
    LrPluginName        = 'ABELr',

    LrLibraryMenuItems = {
        { title = 'ABELr', file = 'Menu.lua', enabledWhen = 'photosAvailable' },
    },

    LrInitPlugin      = 'Init.lua',   -- starts the polling loop
    LrForceInitPlugin = true,

    VERSION = { major = 0, minor = 1, revision = 0 },
}
```

> Other keys (export/publish/metadata) if needed someday: `LrExportServiceProvider`,
> `LrExportFilterProvider`, `LrMetadataProvider`, `LrMetadataTagsetFactory`, `LrHttpHandler`.

---

## 24. Project patterns

### Extract selection data (path + EXIF + develop)
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
            current_develop = photo:getDevelopSettings(),               -- inside a task
        }
    end
    return data
end
-- ⚠️ Call from LrTasks.startAsyncTask (getRawMetadata/getDevelopSettings require a task).
```

### Apply adjustments in batch
```lua
local function applyAdjustmentsBatch(adjustmentsByUuid)
    local catalog = import('LrApplication').activeCatalog()
    local photos  = catalog:getTargetPhotos()
    catalog:withWriteAccessDo('ABELr — Apply', function()
        for _, photo in ipairs(photos) do
            local adj = adjustmentsByUuid[photo:getRawMetadata('uuid')]
            if adj then photo:applyDevelopSettings(adj, 'ABELr') end
        end
    end)
end
```

### HTTP polling loop (GET pending, POST result)
```lua
local function startPollingLoop()
    local LrTasks, LrHttp = import 'LrTasks', import 'LrHttp'
    local LrFunctionContext = import 'LrFunctionContext'

    LrTasks.startAsyncTask(function()
        -- Healthcheck (the App must be running)
        local ready = false
        for _ = 1, 10 do
            local b = LrHttp.get('http://localhost:5000/health', {})
            if b then ready = true break end
            LrTasks.sleep(0.5)
        end
        if not ready then
            import('LrDialogs').message('ABELr', 'App not reachable (app/main.py).', 'warning')
            return
        end

        while true do
            local body, hdrs = LrHttp.get('http://localhost:5000/jobs/pending', {})
            if body and body ~= '' and body ~= 'null' then
                local job = require('dkjson').decode(body)
                if job then
                    local result = handleJob(job)               -- executes the SDK request
                    -- POST the result: MUST be inside postAsyncTaskWithContext
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

### JSON & local modules
```lua
local json = require 'dkjson'                 -- bundled lib at the plugin root
local t = json.decode(body)
local s = json.encode(t)

local PhotoData = require 'PhotoData'     -- your local modules
```

---

## 25. Limitations & constraints

### Accessible via SDK (updated vs old notes)
| Feature | SDK status |
|---|---|
| Develop settings batch (`applyDevelopSettings`) | ✅ on any photo, without the Develop module |
| Read develop settings (`getDevelopSettings`) | ✅ (inside a task) |
| Brush/gradient/radial/range/AI masks (create) | ✅ `LrDevelopController` 11.0+ (Develop module) |
| AI Denoise / Raw Details / Super Resolution | ✅ `toggleEnhance` 14.5+ (Develop module) |
| Remove / Reflection / Distracting people | ✅ 14.1–14.5 (Develop module) |
| Point Color | ✅ 13.2+ (Process Version 3+) |
| Auto Tone / Auto WB | ✅ `setAutoTone` / `setAutoWhiteBalance` |

### Still NOT accessible
| Feature | Status |
|---|---|
| Read the geometry/pixels of an existing mask | ❌ |
| Generative Remove / Generative AI (pixel result) | ❌ (triggerable, not readable) |
| Lens Blur — render | ❌ (params only) |
| Merge HDR / Panorama | ❌ UI only (haptic events exposed) |
| Histogram rendered by Lr | ❌ (decode the RAW on the Python App side) |

### Lua 5.1 constraints (Lr environment)
```lua
-- Missing in 5.1:
--   //  (integer division)  → math.floor(a / b)
--   goto
--   table.pack / table.unpack → unpack()
--   utf8 library               → use LrStringUtils
--   \d in patterns             → use [0-9]
local n = math.floor(10 / 3)         -- 3
local a, b = unpack({ 1, 2 })
```

### Golden rules
- **Any blocking I/O** (HTTP, sleep, large files) inside `LrTasks.startAsyncTask`.
- **Any write** to the catalog/develop inside `catalog:withWriteAccessDo` (do not nest).
- `LrHttp.post` → wrap in `LrFunctionContext.postAsyncTaskWithContext`.
- **Windows paths**: go through `LrPathUtils` (never manual `/` or `\` concatenation).
- **File writing**: `io.open(path,'w')` (not `LrFileUtils.writeFile`).
- **JSON**: no native lib — bundle `dkjson.lua` and `require 'dkjson'`.
- `LrDevelopController` acts on the **active photo of the Develop module**; for batch,
  use `photo:applyDevelopSettings()`.
