# Lr_automation — Plugin Lightroom Classic

## Documentation

| Fichier | Quand le consulter |
|---|---|
| [`documentation/project_overview.md`](documentation/project_overview.md) | Vision globale, architecture, décisions techniques, flux d'utilisation |
| [`documentation/lr15_sdk_api_reference.md`](documentation/lr15_sdk_api_reference.md) | **Référence principale** — tout code Lua plugin : imports, APIs, paramètres Camera Raw 18, patterns, limitations SDK |

> Avant d'écrire du code Lua ou de chercher un nom de paramètre develop, consulter `lr15_sdk_api_reference.md`.
> Les noms de paramètres SDK (ex. `ColorGradeShadowHue`, `AINoiseReductionAmount`) sont dans la section 6 de ce fichier.

---

## Objectif

Plugin Lightroom Classic (Lua + SDK Lr) couplé à une application Python externe pour retouche intelligente et analyse batch.

Flux principal :
1. L'utilisateur interagit via l'interface de l'App Python (GUI)
2. L'App demande des données Lr au plugin via HTTP (chemins RAW, métadonnées, develop settings)
3. Le plugin exécute la requête SDK Lr et retourne le résultat à l'App
4. L'App décode les RAW, analyse, calcule les ajustements optimaux
5. L'App envoie les ajustements au plugin
6. Le plugin applique les ajustements dans Lr via SDK

Fonctionnalités cibles :
- Équilibrage batch de l'exposition (précis, photo par photo)
- Équilibrage batch de la balance des blancs
- Équilibrage et harmonisation de l'étalonnage des couleurs (Color Grading / HSL)
- Carte de prédiction des ajustements sur séries de 500-1000 photos

---

## Stack technique

| Couche | Technologie | Rôle |
|---|---|---|
| Plugin Lr | Lua 5.1 + Adobe Lr Classic SDK 12+ | Pont vers Lightroom, HTTP client |
| App externe — serveur | Python 3.11+ + FastAPI | Serveur HTTP localhost:5000, orchestration |
| App externe — GUI | Python + PySide6 (Qt6) | Interface utilisateur riche |
| App externe — image | rawpy + numpy + OpenCV | Décodage ARW Sony, analyse |
| App externe — analyse | scipy + scikit-learn | Calcul ajustements, carte prédiction |
| Accélération optionnelle | Rust via PyO3 | Algos custom si profiling révèle bottleneck |
| Version Lr cible | Lightroom Classic 12+ (2023+) | |

> **Note Rust :** ne pas intégrer Rust dès le départ. Profiler d'abord (`py-spy`, `cProfile`).
> Ajouter PyO3 uniquement si un algo custom Python pur est identifié comme bottleneck réel.
> Le décodage RAW (LibRaw via rawpy) et OpenCV sont déjà du C/C++ — pas de gain Rust sur ces parties.

---

## Architecture du projet

```
Lr_automation/
│
├── CLAUDE.md
├── documentation/
│   ├── project_overview.md        # Vision globale, décisions architecture
│   └── lr15_sdk_api_reference.md  # Référence API SDK Lr 15 / Camera Raw 18 (Lua)
│
├── plugin/                        # Plugin Lightroom (Lua)
│   ├── Info.lua                   # Manifeste obligatoire (LrToolkitIdentifier, version…)
│   ├── Menu.lua                   # Entrées menu Fichier > Modules externes
│   └── lib/
│       ├── PollingLoop.lua        # LrTasks : boucle HTTP polling toutes 300ms
│       ├── HttpClient.lua         # Wrappers LrHttp (GET/POST JSON vers App)
│       ├── Adjustments.lua        # Application ajustements SDK (withWriteAccessDo)
│       ├── PhotoData.lua          # Extraction path, EXIF, develop settings via SDK
│       └── Utils.lua              # Helpers, sérialisation JSON
│
└── app/                           # Application Python externe
    ├── main.py                    # Point d'entrée : lance GUI + serveur FastAPI
    ├── server/
    │   ├── api.py                 # Routes FastAPI : /jobs, /apply, /status
    │   └── job_queue.py           # Queue des jobs en attente pour le plugin
    ├── gui/
    │   ├── main_window.py         # Fenêtre principale PySide6
    │   ├── photo_panel.py         # Affichage sélection / aperçu
    │   └── analysis_panel.py      # Visualisation analyse, histogrammes, carte prédiction
    ├── core/
    │   ├── raw.py                 # Décodage ARW Sony via rawpy (LibRaw)
    │   ├── analysis.py            # Analyse exposition, WB, couleurs (numpy + OpenCV)
    │   ├── prediction.py          # Modèle prédiction sur série 500-1000 photos
    │   └── adjustments.py         # Calcul et formatage corrections finales
    ├── rust_ext/                  # (optionnel, plus tard) Module PyO3 si bottleneck
    │   └── src/lib.rs
    └── requirements.txt
```

---

## Architecture de communication

### Principe fondamental

```
Plugin Lua = TOUJOURS client HTTP
App Python = TOUJOURS serveur HTTP (localhost:5000)
```

Le plugin ne peut pas exposer un serveur facilement (LrSocket possible mais complexe).
Solution : plugin tourne une boucle de polling via `LrTasks`.

### Flux d'un job (exemple : obtenir le path RAW d'une photo sélectionnée)

```
App GUI : user clique "Analyser"
  → App ajoute job { id: "abc", type: "get_selected_photos" } dans job_queue interne

Plugin (boucle LrTasks, 300ms) :
  GET http://localhost:5000/jobs/pending
  ← { job_id: "abc", type: "get_selected_photos" }

  Plugin exécute via SDK Lr :
  photos = catalog:getTargetPhotos()
  → collecte paths, EXIF, develop settings

  POST http://localhost:5000/jobs/abc/result
  → { photos: [ { id, path, exif, current_develop } ] }

App reçoit les données :
  → Décode RAW (rawpy), analyse (numpy/OpenCV)
  → Calcule ajustements optimaux

App envoie ajustements :
  POST http://localhost:5000/apply   ← non, c'est l'App qui envoie au plugin
```

> Correction : pour les ajustements, l'App doit aussi passer par la queue de jobs.
> L'App crée un job `{ type: "apply_adjustments", adjustments: [...] }`.
> Le plugin le récupère via polling et applique.

### Endpoints FastAPI (App)

| Endpoint | Méthode | Description |
|---|---|---|
| `/jobs/pending` | GET | Plugin récupère prochain job à exécuter |
| `/jobs/{id}/result` | POST | Plugin soumet le résultat d'un job |
| `/status` | GET | État de l'App (prête, en cours d'analyse…) |
| `/health` | GET | Healthcheck (plugin vérifie si App est démarrée) |

---

## SDK Lightroom — APIs clés

> Référence complète dans [`documentation/lr15_sdk_api_reference.md`](documentation/lr15_sdk_api_reference.md).
> Couvre : tous les imports, LrApplication, LrCatalog, LrPhoto, LrDevelopController,
> LrTasks, LrHttp, LrSocket, LrDialogs, LrProgressScope, LrFileUtils, LrLogger, LrShell,
> patterns complets (polling, batch, dispatch jobs), limitations SDK.

Rappel des APIs les plus utilisées dans ce projet :

```lua
-- Sélection active
local catalog = LrApplication.activeCatalog()
local photos  = catalog:getTargetPhotos()

-- Lire données photo
local path    = photo:getRawMetadata('path')
local uuid    = photo:getRawMetadata('uuid')
local develop = photo:getDevelopSettings()

-- Écrire ajustements (transaction obligatoire)
catalog:withWriteAccessDo('Apply adjustments', function()
    photo:applyDevelopSettings({ Exposure = 0.35, Temperature = 5600 })
end)

-- HTTP client (GET/POST vers App Python)
local body, headers = LrHttp.get('http://localhost:5000/jobs/pending', {})
local body, headers = LrHttp.post(url, jsonPayload, {
    { field = 'Content-Type', value = 'application/json' }
})

-- Async (tout I/O bloquant ici)
LrTasks.startAsyncTask(function()
    while true do
        -- polling loop
        LrTasks.sleep(0.3)
    end
end)
```

---

## Contraintes Lua / SDK Lr

- **Lua 5.1** : pas de `//` (division entière), pas de `goto`, pas de `utf8` stdlib
- **Pas de multithreading** : tout I/O bloquant dans `LrTasks.startAsyncTask`
- **withWriteAccessDo obligatoire** : toute écriture catalog ou develop settings dans une transaction
- **Chemins Windows** : utiliser `LrPathUtils` — ne jamais concaténer `/` manuellement
- **LrDevelopController** : opère sur la photo active dans module Développement uniquement
  → Pour batch, utiliser `photo:applyDevelopSettings()` directement (pas besoin module Développement)
- **JSON** : pas de lib JSON native Lua — utiliser une lib embarquée (ex. `dkjson.lua`)
- **Pas de `require` standard** : importer les modules SDK avec `import 'LrXxx'`

---

## Format JSON — échange plugin ↔ App

### Job envoyé au plugin (App → plugin via polling)
```json
{
  "job_id": "uuid-v4",
  "type": "get_selected_photos"
}
```

```json
{
  "job_id": "uuid-v4",
  "type": "apply_adjustments",
  "adjustments": [
    {
      "photo_id": "lr-internal-uuid",
      "develop": {
        "Exposure": 0.35,
        "Temperature": 5650,
        "Tint": -5,
        "Highlights": -20,
        "Shadows": 15
      }
    }
  ]
}
```

### Résultat retourné par le plugin (plugin → App)
```json
{
  "job_id": "uuid-v4",
  "status": "ok",
  "photos": [
    {
      "photo_id": "lr-internal-uuid",
      "path": "C:/photos sony/DSC00123.ARW",
      "exif": {
        "iso": 800,
        "aperture": 2.8,
        "shutter_speed": "1/200",
        "focal_length": 85,
        "camera": "ILCE-7M4"
      },
      "current_develop": {
        "Exposure": 0.0,
        "Temperature": 5500,
        "Tint": 0,
        "Highlights": 0,
        "Shadows": 0
      }
    }
  ]
}
```

---

## Paramètres de développement Lr (noms SDK)

> Liste complète avec plages de valeurs dans [`documentation/lr15_sdk_api_reference.md`](documentation/lr15_sdk_api_reference.md) — section 6.
> Couvre : exposition, WB, HSL, Color Grading, Point Color, Tone Curve, Denoise AI,
> Lens Corrections, calibration caméra, recadrage, effets, ProcessVersion.

Groupes principaux :

| Groupe | Paramètres SDK |
|---|---|
| Exposition | `Exposure`, `Contrast`, `Highlights`, `Shadows`, `Whites`, `Blacks`, `Clarity`, `Dehaze` |
| Balance des blancs | `Temperature`, `Tint`, `WhiteBalance` |
| Couleur | `Vibrance`, `Saturation` |
| HSL (8 canaux) | `HueAdjustmentRed/…`, `SaturationAdjustmentRed/…`, `LuminanceAdjustmentRed/…` |
| Color Grading | `ColorGradeShadowHue/Sat/Lum`, `ColorGradeMidtoneHue/Sat/Lum`, `ColorGradeHighlightHue/Sat/Lum` |
| Ton / Courbe | `ParametricShadows`, `ParametricDarks`, `ParametricLights`, `ParametricHighlights` |
| Netteté | `Sharpness`, `SharpenRadius`, `SharpenDetail`, `SharpenEdgeMasking`, `Texture` |
| Bruit | `LuminanceSmoothing`, `ColorNoiseReduction` |
| Denoise AI | `AINoiseReduction`, `AINoiseReductionAmount` |
| Calibration | `CameraProfile`, `RedHue/Sat`, `GreenHue/Sat`, `BlueHue/Sat` |

---

## Convention de nommage

| Contexte | Convention |
|---|---|
| Fichiers Lua | `PascalCase.lua` |
| Fonctions Lua | `camelCase` |
| Variables locales Lua | `camelCase` |
| Constantes Lua | `UPPER_SNAKE_CASE` |
| Fichiers Python | `snake_case.py` |
| Classes Python | `PascalCase` |
| Fonctions/variables Python | `snake_case` |
| Clés JSON échangées | `snake_case` |
| Noms paramètres SDK Lr dans JSON | `PascalCase` (identique au SDK) |

---

## Workflow de développement

### Plugin Lua
1. Modifier les fichiers dans `plugin/`
2. Lr : **Fichier > Gestionnaire des modules externes** > Recharger
3. Tester via **Fichier > Modules externes** > entrée définie dans `Menu.lua`
4. Logs : `LrLogger` ou `print()` → **Aide > Console Lua** dans Lr

### App Python
1. `cd app && uvicorn main:app --reload --port 5000`
2. GUI PySide6 se lance au démarrage de `main.py`
3. Tester les endpoints API indépendamment : `curl http://localhost:5000/health`
4. Mock du plugin : POST manuellement sur `/jobs/{id}/result` pour simuler réponses

### Test end-to-end
1. Lancer `python app/main.py`
2. Recharger plugin dans Lr
3. Sélectionner photos dans Lr
4. Déclencher action depuis GUI App

---

## À faire (backlog initial)

### Plugin Lua
- [ ] Créer `plugin/Info.lua` (manifeste : LrToolkitIdentifier, LrSdkVersion, LrSdkMinimumVersion)
- [ ] Créer `plugin/Menu.lua` (entrée menu + démarrage boucle polling)
- [ ] Implémenter `lib/HttpClient.lua` (GET/POST via LrHttp + sérialisation JSON)
- [ ] Implémenter `lib/PollingLoop.lua` (LrTasks, 300ms, dispatch jobs)
- [ ] Implémenter `lib/PhotoData.lua` (path, EXIF, develop settings)
- [ ] Implémenter `lib/Adjustments.lua` (withWriteAccessDo, applyDevelopSettings batch)
- [ ] Embarquer `dkjson.lua` (parser JSON pour Lua)

### App Python
- [ ] Setup projet : `requirements.txt`, venv, structure dossiers
- [ ] Créer `server/api.py` : endpoints `/health`, `/jobs/pending`, `/jobs/{id}/result`
- [ ] Créer `server/job_queue.py` : queue thread-safe (asyncio.Queue)
- [ ] Créer `main.py` : démarrage FastAPI (thread) + GUI PySide6 (main thread)
- [ ] Créer `core/raw.py` : décodage ARW Sony via rawpy
- [ ] Créer `core/analysis.py` : analyse exposition et WB (histogrammes, numpy)
- [ ] Créer `gui/main_window.py` : fenêtre principale PySide6 minimale
- [ ] Prototype end-to-end : plugin → App → décode RAW → retourne résultat
