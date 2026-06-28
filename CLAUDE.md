# Lr_automation — Plugin Lightroom Classic

## Documentation

| Fichier | Quand le consulter |
|---|---|
| [`documentation/project_overview.md`](documentation/project_overview.md) | Vision globale, architecture, décisions techniques, flux d'utilisation |
| [`documentation/lr15_sdk_api_reference.md`](documentation/lr15_sdk_api_reference.md) | **Référence principale** — tout code Lua plugin : imports, APIs, paramètres Camera Raw 18, patterns, limitations SDK |

> Avant d'écrire du code Lua ou de chercher un nom de paramètre develop, consulter `lr15_sdk_api_reference.md`.
> Les méthodes marquées ⚠️ dans ce fichier sont **non vérifiées** — les tester ou les confirmer dans la doc Adobe SDK officielle avant usage.

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
├── launch_app.ps1                 # Lancé par le plugin pour démarrer l'App Python
├── createVenv.ps1                 # Crée le venv Python (setup initial)
├── documentation/
│   ├── project_overview.md        # Vision globale, décisions architecture
│   ├── lr15_sdk_api_reference.md  # Référence API SDK Lr 15 / Camera Raw 18 (Lua)
│   └── Lr_SDK_API/                # SDK Adobe officiel (HTML + PDF + samples)
│
├── LrAutomation.lrplugin/         # Dossier chargé par Lightroom (à la racine du projet)
│   ├── Info.lua                   # Manifeste (LrToolkitIdentifier, menus, version)
│   ├── MenuConnect.lua            # Menu "Démarrer / connecter l'application"
│   ├── MenuRelaunch.lua           # Menu "Relancer l'application"
│   ├── ShowMessage.lua            # Menu "test" (debug)
│   ├── PluginInfoProvider.lua     # Section custom Gestionnaire de modules externes
│   ├── Actions.lua                # Actions haut niveau (connect, relaunch, checkStatus)
│   ├── AppLauncher.lua            # Démarre / arrête / relance le process Python
│   ├── PollingLoop.lua            # Boucle polling 300ms, dispatch jobs, heartbeat
│   ├── HttpClient.lua             # Wrappers LrHttp (GET/POST JSON vers App)
│   ├── Adjustments.lua            # Application ajustements SDK (withWriteAccessDo)
│   ├── PhotoData.lua              # Extraction path, EXIF, develop settings via SDK
│   ├── Json.lua                   # Encodeur/décodeur JSON pour Lua (lib embarquée)
│   └── Utils.lua                  # Helpers (log, logf, test popup)
│
└── app/                           # Application Python externe
    ├── main.py                    # Point d'entrée : lance GUI + serveur FastAPI
    ├── requirements.txt
    ├── server/
    │   ├── api.py                 # Routes FastAPI (voir endpoints ci-dessous)
    │   ├── job_queue.py           # Queue thread-safe, heartbeat bridge, résultats
    │   └── models.py              # Modèles Pydantic : Job, JobResult, PhotoResult…
    ├── gui/
    │   ├── main_window.py         # Fenêtre principale PySide6
    │   ├── photo_panel.py         # Affichage sélection / aperçu
    │   ├── analysis_panel.py      # Visualisation analyse, histogrammes, carte prédiction
    │   └── job_worker.py          # Worker Qt pour lancer des jobs sans bloquer le GUI
    ├── core/
    │   ├── raw.py                 # Décodage ARW Sony via rawpy (LibRaw)
    │   ├── analysis.py            # Analyse exposition, WB, couleurs (numpy + OpenCV)
    │   ├── prediction.py          # Modèle prédiction sur série 500-1000 photos
    │   └── adjustments.py         # Calcul et formatage corrections finales
    └── tools/
        └── mock_plugin.py         # Mock du plugin : simule polling + résultats (tests)
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
| `/health` | GET | Healthcheck — plugin vérifie si App est démarrée |
| `/status` | GET | État App : pending jobs, bridge connecté, dernier poll |
| `/bridge` | GET | État du pont plugin (battement de cœur, dernière activité) |
| `/jobs/pending` | GET | Plugin récupère prochain job (204 si vide) — marque heartbeat |
| `/jobs/{id}/result` | POST | Plugin soumet le résultat d'un job |
| `/shutdown` | POST | Arrêt propre du process Python (utilisé par plugin pour relancer) |

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
- **JSON** : pas de lib JSON native Lua — lib embarquée `Json.lua` (à la racine du plugin)
- **Pas de `require` standard** : importer les modules SDK avec `import 'LrXxx'`
- **`LrFunctionContext.postAsyncTaskWithContext`** : requis pour tout appel `LrHttp` (GET/POST) — `LrTasks.startAsyncTask` seul ne suffit pas pour le HTTP
- **Heartbeat bridge** : `_G.LR_AUTOMATION_BRIDGE_HEARTBEAT` mis à jour à chaque tour de boucle — utilisé pour détecter une boucle morte sans cleanup propre

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
  "payload": {
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
| Denoise AI | ⚠️ noms de paramètres non vérifiés — consulter doc Adobe SDK |
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
1. Modifier les fichiers dans `LrAutomation.lrplugin/` (racine du projet)
2. Lr : **Fichier > Gestionnaire des modules externes** > sélectionner `LrAutomation.lrplugin/` > Recharger
3. Tester via **Bibliothèque > Modules externes** > entrées "Démarrer / connecter" ou "Relancer"
4. Logs : `Utils.logf(...)` → **Aide > Console Lua** dans Lr

### App Python
1. Lancer via `launch_app.ps1` (utilisé aussi par le plugin) ou `python -m app.main` depuis la racine
2. GUI PySide6 se lance au démarrage ; serveur FastAPI tourne dans un thread daemon
3. Tester les endpoints : `curl http://localhost:5000/health`
4. Mock du plugin : `python -m app.tools.mock_plugin` pour simuler polling + résultats

### Test end-to-end
1. Lancer l'App (via menu Lr "Démarrer / connecter" ou `launch_app.ps1` directement)
2. Vérifier `GET /bridge` → `connected: true` (pont actif)
3. Sélectionner photos dans Lr, déclencher action depuis GUI App
4. Vérifier résultat via `GET /status` (pending_jobs = 0 si traité)

---

## Backlog

### Plugin Lua
- [x] `Info.lua` — manifeste complet avec menus Bibliothèque / Fichier / Aide
- [x] `MenuConnect.lua` / `MenuRelaunch.lua` — entrées menu
- [x] `HttpClient.lua` — GET/POST JSON via LrHttp
- [x] `PollingLoop.lua` — boucle 300ms, dispatch jobs, heartbeat, garde anti-doublon
- [x] `Actions.lua` — connect, relaunch, checkStatus
- [x] `AppLauncher.lua` — start / stop / relaunch process Python via `launch_app.ps1`
- [x] `PhotoData.lua` — path, EXIF, develop settings
- [x] `Adjustments.lua` — withWriteAccessDo, applyDevelopSettings batch
- [x] `Json.lua` — parser/encodeur JSON embarqué
- [x] `Utils.lua` — logf, test popup
- [x] `PluginInfoProvider.lua` — section custom Gestionnaire modules externes

### App Python
- [x] Setup : `requirements.txt`, venv, structure dossiers
- [x] `server/api.py` — tous les endpoints (health, bridge, status, jobs, shutdown)
- [x] `server/job_queue.py` — queue thread-safe, heartbeat, résultats
- [x] `server/models.py` — modèles Pydantic (Job, JobResult, PhotoResult, PhotoAdjustment)
- [x] `main.py` — FastAPI (thread daemon) + GUI PySide6 (main thread)
- [x] `core/raw.py` — décodage ARW Sony via rawpy
- [x] `core/analysis.py` — analyse exposition et WB
- [x] `gui/main_window.py` — fenêtre principale PySide6
- [x] `gui/job_worker.py` — worker Qt pour jobs async sans bloquer le GUI
- [x] `tools/mock_plugin.py` — mock du plugin pour tests sans Lightroom
- [x] `launch_app.ps1` — script de lancement (venv auto-détecté)

### À faire
- [ ] `core/adjustments.py` — calcul des ajustements optimaux (algo exposition, WB)
- [ ] `core/prediction.py` — modèle prédiction sur série 500-1000 photos
- [ ] GUI : boutons "Analyser" et "Appliquer" câblés sur les vrais jobs
- [ ] Prototype end-to-end complet : sélection Lr → décodage RAW → calcul → apply
