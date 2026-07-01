# Lr_automation — Plugin Lightroom Classic

## Documentation

| Fichier | Quand le consulter |
|---|---|
| [`documentation/project_overview.md`](documentation/project_overview.md) | Vision globale, architecture, décisions techniques, flux d'utilisation |
| [`documentation/lr15_sdk_api_reference.md`](documentation/lr15_sdk_api_reference.md) | **Référence principale** — tout code Lua plugin : imports, APIs, paramètres Camera Raw 18, patterns, limitations SDK |
| [`app/README.md`](app/README.md) | Install, lancement, test sans Lr, structure du pipeline image `core/` |

> Avant d'écrire du code Lua ou de chercher un nom de paramètre develop, consulter `lr15_sdk_api_reference.md`.
> Les méthodes marquées ⚠️ dans ce fichier sont **non vérifiées** — les tester ou les confirmer dans la doc Adobe SDK officielle avant usage.

---

## Objectif

Plugin Lightroom Classic (Lua + SDK Lr) couplé à une application Python externe pour retouche intelligente et analyse batch.

Flux principal :
1. L'utilisateur interagit via l'interface de l'App Python (GUI)
2. L'App demande des données Lr au plugin via HTTP (chemins RAW, métadonnées, develop settings, chemin du catalogue)
3. Le plugin exécute la requête SDK Lr et retourne le résultat à l'App
4. L'App **décode le RAW** (rawpy → ProPhoto linéaire) et calcule les ajustements optimaux
5. L'App envoie les ajustements au plugin (via la même queue de jobs)
6. Le plugin applique les ajustements dans Lr via SDK

Fonctionnalités cibles :
- **Balance des blancs batch par photo** (cœur actuel) : calibrage sur 5-8 seeds →
  WB cohérente sur toute la série depuis l'as-shot boîtier (`core/wb_model`)
- Exposition batch (souvent quasi-constante sur un event ; médiane des seeds)
- Détection du régime (physique auto vs artistique → repli)
- Plus tard : harmonisation étalonnage couleur (Color Grading / HSL)

> Voir le verdict des essais dans le Backlog : la prédiction depuis les statistiques
> pixel globales d'une photo isolée est une **impasse prouvée** (n=1142) ; la WB se
> calibre par seeds, ancrée sur la physique du boîtier.

---

## Stack technique

| Couche | Technologie | Rôle |
|---|---|---|
| Plugin Lr | Lua 5.1 + Adobe Lr Classic SDK 12+ | Pont vers Lightroom, HTTP client |
| App externe — serveur | Python 3.11+ + FastAPI | Serveur HTTP 127.0.0.1:5000, orchestration |
| App externe — GUI | Python + PySide6 (Qt6) | Interface utilisateur riche |
| App externe — image | rawpy + numpy + OpenCV | Décodage ARW Sony (source d'analyse), ProPhoto linéaire |
| App externe — previews | tifffile + imagecodecs (libjxl) + SQLite | Localisation des bundles `.lrdata`, décodage aperçu rendu / inspection Smart Preview |
| App externe — analyse | scipy + scikit-learn | Calcul ajustements, carte prédiction |
| Accélération optionnelle | Rust via PyO3 | Algos custom si profiling révèle bottleneck |
| Version Lr cible | Lightroom Classic 12+ (2023+) ; pipeline previews vérifié sur Lr 13 | |

> **Note Rust :** ne pas intégrer Rust dès le départ. Profiler d'abord (`py-spy`, `cProfile`).
> Ajouter PyO3 uniquement si un algo custom Python pur est identifié comme bottleneck réel.
> Le décodage RAW (LibRaw via rawpy), la décompression JPEG XL et OpenCV sont déjà du C/C++ — pas de gain Rust sur ces parties.

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
│   ├── ShowMessage.lua            # Menu "test" (popup Hello World, debug)
│   ├── PluginInfoProvider.lua     # Section custom Gestionnaire de modules externes
│   ├── Actions.lua                # Actions haut niveau (connect, relaunch, checkStatus)
│   ├── AppLauncher.lua            # Démarre / arrête / relance le process Python
│   ├── PollingLoop.lua            # Boucle polling 300ms, dispatch jobs, heartbeat
│   ├── HttpClient.lua             # Wrappers LrHttp (GET/POST JSON vers App)
│   ├── Adjustments.lua            # Application ajustements SDK (withWriteAccessDo)
│   ├── PhotoData.lua              # Extraction path, EXIF, develop settings, catalog_path
│   ├── Json.lua                   # Encodeur/décodeur JSON pour Lua (lib embarquée)
│   └── Utils.lua                  # Helpers (log, logf, chemins projet, test popup)
│
└── app/                           # Application Python externe
    ├── main.py                    # Point d'entrée : serveur FastAPI (thread) + GUI (thread principal)
    ├── README.md                  # Install / lancement / structure core
    ├── requirements.txt
    ├── server/
    │   ├── api.py                 # Routes FastAPI (voir endpoints ci-dessous)
    │   ├── job_queue.py           # Queue thread-safe, heartbeat du pont, résultats
    │   └── models.py              # Modèles Pydantic : Job, JobResult, PhotoResult, ExifData…
    ├── gui/
    │   ├── main_window.py         # Fenêtre PySide6 : seeds (ajouter/supprimer), analyser, apply par axe, pont
    │   ├── job_worker.py          # QThread : soumet un job, attend le résultat du plugin
    │   ├── analysis_worker.py     # QThread : décode + analyse chaque photo (RAW → ProPhoto linéaire)
    │   ├── autocorrect_worker.py  # QThread : RAW+JPEG boîtier+aperçu (zone nette) → cache → autocorrect.plan
    │   ├── photo_panel.py         # Aperçu / liste photos — stub réservé
    │   └── analysis_panel.py      # Histogrammes / outliers WB — stub réservé
    ├── core/
    │   ├── color.py               # Espaces couleur de l'analyse : ProPhoto linéaire, luminance Y, → sRGB display
    │   ├── raw.py                 # Décodage ARW Sony via rawpy : load_linear (ProPhoto, analyse) / load_rgb (sRGB u8)
    │   ├── image_source.py        # Source pixel de l'analyse : RAW → ProPhoto linéaire (LoadedImage)
    │   ├── analysis.py            # Métriques exposition (Y) + balance des blancs (gray-world), en linéaire
    │   ├── catalog.py             # Localise .lrcat + bundles .lrdata, ouvre les SQLite en lecture seule
    │   ├── previews.py            # Résout id_global → fichiers preview ; aperçu rendu (verif) + Smart Preview (inspection)
    │   ├── sharpness.py           # Masque « zone nette » (Laplacien, top 25%) CPU+GPU — restreint les histogrammes
    │   ├── seed_match.py          # k-NN sur seeds (analyse RAW zone nette) → cible Temp/Tint/tone/bandes
    │   ├── exposure.py            # Espace rendu : ΔEV depuis L* courant → L* cible (embedded ou seed_match)
    │   ├── hsl.py                 # Deltas HSL par bande vs cible (embedded ou seed_match), réponse calibrée
    │   ├── autocorrect.py         # Orchestration expo+WB+HSL par photo, modes seeds/embedded
    │   ├── adjustments.py         # Helper de formatage du dict develop (PascalCase SDK)
    │   ├── gpu.py                 # Contexte CUDA strict, budget VRAM, pool de streams
    │   ├── gpu_raw.py             # RAW bayer → GPU : demosaic + WB + matrice → ProPhoto + stats + tone/bandes zone nette
    │   ├── gpu_jpeg.py            # Décodage JPEG sur GPU (nvJPEG) + extraction flux JPEG
    │   ├── gpu_schedule.py        # Scheduler VRAM-aware : unpack CPU borné → vagues GPU
    │   ├── render_metrics_gpu.py  # Portage torch CUDA de render_metrics (tone/neutral/bandes, masque zone nette)
    │   └── cache.py               # Cache SQLite (4 tables, clé uuid+hash) + flag is_seed, dans le dossier catalogue
    │
    │   (`wb_model.py`/`regime.py` : conservés mais déréférencés du chemin live — gardés
    │    pour `tools/validate_wb_seeds.py`/`cross_catalog_wb.py`/`seed_curve.py`, remplacés
    │    côté GUI par `seed_match.py`, cf. Backlog)
    └── tools/                     # Scripts hors-app : mock, vérité terrain, recherche/validation
        ├── mock_plugin.py         # Mock du plugin : simule polling + résultats (tests sans Lr)
        ├── analyze_ground_truth.py# Vérité terrain : RAW → réglages develop → JPEG final (export CSV)
        ├── series_audit.py        # Audit série : régressions expo/WB, détection régime
        ├── validate_wb_seeds.py   # Validation du modèle WB-seeds sur un catalogue (LOO seeds)
        ├── cross_catalog_wb.py    # Généralisation croisée du modèle WB entre catalogues
        ├── seed_curve.py          # Courbe k-seeds : combien de seeds pour calibrer
        └── calibrate_sp_vs_raw.py # Calibration Smart Preview ↔ RAW (a tranché : RAW seul)
```

---

## Architecture de communication

### Principe fondamental

```
Plugin Lua = TOUJOURS client HTTP
App Python = TOUJOURS serveur HTTP (127.0.0.1:5000)
```

Le plugin ne peut pas exposer un serveur facilement (LrSocket possible mais complexe).
Solution : le plugin tourne une boucle de polling via `LrTasks`.

GUI et serveur FastAPI vivent dans le **même process** Python (serveur dans un thread
daemon, GUI sur le thread principal Qt). Ils partagent l'instance `job_queue` en mémoire —
pas de HTTP entre eux, juste un `Lock` + `threading.Event`.

### Flux d'un job (exemple : analyser la sélection)

```
App GUI : user clique « Analyser la sélection »
  → JobWorker (QThread) : job_queue.submit('get_selected_photos') puis wait_result()

Plugin (PollingLoop, 300ms) :
  GET /jobs/pending
  ← { job_id, type: "get_selected_photos" }
  Exécute via SDK : catalog:getTargetPhotos() → path, EXIF, develop, catalog_path
  POST /jobs/{id}/result  → débloque le JobWorker du GUI

App reçoit les PhotoResult :
  → AnalysisWorker (QThread) : pour chaque photo, image_source.load_for_analysis()
     → décodage RAW via rawpy en ProPhoto linéaire (float32)
  → analysis.exposure_stats (luminance Y) + gray_world_wb, émis photo par photo

Calibrage WB (bouton « Calibrer WB ») :
  → CalibrateWorker : collect_seeds (WB Custom) → wb_model.calibrate → regime.detect
     → seeds.plan_adjustments pour les non-seeds
  → bouton « Appliquer WB au reste » : job apply_adjustments
     Plugin le récupère via polling et applique via photo:applyDevelopSettings().
```

> **Les ajustements passent aussi par la queue de jobs.** L'App ne « pousse » jamais
> vers le plugin : elle crée un job `apply_adjustments`, le plugin le récupère au
> prochain poll et applique.

### Endpoints FastAPI (App)

| Endpoint | Méthode | Description |
|---|---|---|
| `/health` | GET | Healthcheck — le plugin vérifie au démarrage que l'App tourne |
| `/status` | GET | État App : pending jobs, pont connecté, dernier poll |
| `/bridge` | GET | État du pont plugin (battement de cœur, secondes depuis dernier poll) |
| `/jobs/pending` | GET | Plugin récupère le prochain job (204 si vide) — marque le heartbeat |
| `/jobs/{id}/result` | POST | Plugin soumet le résultat d'un job |
| `/shutdown` | POST | Arrêt propre du process Python (utilisé par le plugin pour « Relancer ») |

---

## Pipeline image — source et espace d'analyse (`core/`)

**Source = RAW d'origine via rawpy.** Décision validée par calibration sur catalogue
réel (`tools/calibrate_sp_vs_raw.py`). rawpy fait un développement complet et cohérent
(WB, matrice couleur, démosaïquage) ; c'est la seule source qui donne des métriques
justes et comparables entre photos.

### Pourquoi pas la Smart Preview

Tentant pour la vitesse (~2.5MP), mais **inexploitable en l'état** :
- Son DNG est en `PhotometricInterpretation = 34892` (**LinearRaw**) : raw caméra-natif
  démosaïqué, **avant** balance des blancs et **avant** matrice couleur — pas un RGB.
- LibRaw/rawpy **ne décode pas** ses tuiles JPEG XL (compression 52546).
- Calibration : SP brute vs RAW développé → Δexposition +2.3 stops, incohérent
  (σ ≈ 0.7) ; ratios WB ingérables. Même développée à la main (AsShotNeutral +
  ForwardMatrix + opcodes), l'écart reste incohérent (σ ≈ 1.3 stop).
- À noter : les Smart Previews ne sont générées que si l'utilisateur le demande
  (souvent absentes) — le gain de vitesse serait de toute façon partiel.

`previews.py` / `catalog.py` restent utiles pour localiser les bundles et décoder
l'**aperçu rendu** (`Previews.lrdata`, JPEG, réglages appliqués) — utile pour vérifier
le *résultat* d'une correction, pas pour la mesurer.

### Espace et format d'analyse (validés par calibration)

| Décision | Valeur | Raison |
|---|---|---|
| Format | **float32 scène-linéaire 0-1** | Pas de gamma (WB/clipping justes), pas d'écrêtage 8-bit (ombres) |
| Primaires | **ProPhoto (gamut large)** | sRGB **écrête** les couleurs saturées → biais jusqu'à ×2 sur les ratios gray-world. Exposition OK en sRGB, mais on unifie en ProPhoto |
| Luminance | **Y de XYZ** (ligne ProPhoto→XYZ) | Exacte, indépendante du gamut (= luma Rec.709 à 0.05 stop près) |
| Balance des blancs | `use_camera_wb=True` | **Obligatoire** : sans elle, les ratios mesurent le capteur, pas la scène (g/b instable 1.5→11.6). Avec, gray-world = cast résiduel vs as-shot |
| Affichage GUI | uint8 sRGB à la demande | `LoadedImage.display_u8` (ProPhoto→sRGB). Jamais pour l'analyse |

Constantes et conversions dans [`core/color.py`](app/core/color.py). Décodage dans
[`core/raw.py`](app/core/raw.py) (`load_linear`). Coût mesuré : **~1.5 s/photo**
(half_size, ILCE-7M4 33MP) → parallélisation à prévoir pour les séries 500-1000.

### Accélération — pipeline GPU (décision utilisateur : GPU-strict)

> **Mise à jour (refonte GPU).** L'ancien verdict « le GPU n'aide pas » valait pour le chemin
> LibRaw `postprocess` (C++ CPU-only). Sur décision utilisateur, le **décodage pixel est passé
> sur GPU** : on n'appelle plus `postprocess`, on réécrit le pipeline. Modules : `core/gpu.py`
> (contexte CUDA strict, budget VRAM, streams), `core/gpu_raw.py` (bayer→GPU), `core/gpu_jpeg.py`
> (nvJPEG), `core/gpu_schedule.py` (scheduler VRAM-aware), `core/render_metrics_gpu.py`.

| Étape | Où | Détail |
|---|---|---|
| Décompression/unpack ARW → plan bayer 16-bit | **CPU (irréductible)** | `rawpy.raw_image_visible` + métadonnées. Aucun codec GPU pour l'ARW Sony. Pas de demosaic ici. Pool **borné** aux cœurs physiques (jamais 32 → c'était la cause du gel). |
| Black-level, WB CFA, **demosaic** (bilinéaire), matrice caméra→ProPhoto, stats (Y, gray-world) | **GPU (torch CUDA)** | `gpu_raw.process_bayer_gpu`. Matrice = réplique dcraw `cam_xyz_coeff`. |
| Décodage JPEG (aperçu rendu + JPEG boîtier) | **GPU (nvJPEG)** | `torchvision.io.decode_jpeg(device='cuda')`, batché. `gpu_jpeg`. |
| tone / neutral / bandes (CIELAB) | **GPU** | `render_metrics_gpu` — portage torch de `render_metrics`, validé exact vs numpy. |

**GPU-strict** : aucun repli CPU de calcul (`gpu.require_cuda` lève si CUDA absent). La VRAM (8 Go)
est gérée par `gpu_schedule` (vagues dimensionnées au budget, unpack CPU borné qui pré-charge la
RAM hôte = « combinaison RAM + VRAM »). Parité vérifiée par `tools/validate_gpu_vs_libraw`
(exposition Y : corr 1.000 ; gray-world : corr 0.97-0.9995, petit biais constant absorbé par le
calibrage seeds).

> **Cache obligatoire** (`core/cache.py`) : `LrAutomation_cache.db` dans le dossier du catalogue,
> 4 tables (`LightroomPicture`, `SourceRAW`, `InCameraJPEG`, `PreviewJPEG`), clé `uuid` + `hash`
> par élément (signature fichier). Les workers consultent le cache d'abord → 2e passage = zéro
> décode. C'est le vrai gain sur les séries 500-1000, en plus du GPU.

### Aperçu rendu et résolution d'identifiant (pour la vérification / l'inspection)

Le `uuid` qui nomme les fichiers de preview n'est **pas** celui que le plugin envoie
(`getRawMetadata('uuid')` = `id_global`). Le pont se fait en deux sauts SQLite
(`previews.PreviewIndex`) :

```
id_global  --(.lrcat: Adobe_images)-->  id_local
id_local   --(previews.db: ImageCacheEntry)-->  uuid de cache + digest
```

Le `uuid` de cache nomme les fichiers d'aperçu (`{uuid}-{digest}_{taille}`) et le DNG
Smart Preview (`{uuid}.dng`), dans le sous-dossier `{uuid[0]}/{uuid[:4]}`. `.lrcat` et
`previews.db` sont du SQLite standard → ouverts en lecture seule immuable
(`mode=ro&immutable=1`) : aucun verrou, cohabite avec Lightroom ouvert.

---

## SDK Lightroom — APIs clés

> Référence complète dans [`documentation/lr15_sdk_api_reference.md`](documentation/lr15_sdk_api_reference.md).
> Couvre : tous les imports, LrApplication, LrCatalog, LrPhoto, LrDevelopController,
> LrTasks, LrHttp, LrSocket, LrDialogs, LrProgressScope, LrFileUtils, LrLogger, LrShell,
> patterns complets (polling, batch, dispatch jobs), limitations SDK.

Rappel des APIs les plus utilisées dans ce projet :

```lua
-- Sélection active + chemin du catalogue (pour localiser les .lrdata côté App)
local catalog     = LrApplication.activeCatalog()
local photos      = catalog:getTargetPhotos()
local catalogPath = catalog:getPath()

-- Lire données photo
local path    = photo:getRawMetadata('path')
local uuid    = photo:getRawMetadata('uuid')   -- = id_global (Adobe_images.id_global)
local develop = photo:getDevelopSettings()

-- Écrire ajustements (transaction obligatoire). Noms SDK = PV2012 (Exposure2012).
-- WhiteBalance='Custom' requis pour que Temperature/Tint prennent effet.
catalog:withWriteAccessDo('Apply adjustments', function()
    photo:applyDevelopSettings({ Exposure2012 = 0.35, WhiteBalance = 'Custom', Temperature = 5600, Tint = -5 })
end)

-- HTTP client (GET/POST vers App Python)
local body, headers = LrHttp.get('http://127.0.0.1:5000/jobs/pending', {}, 5)
local body, headers = LrHttp.post(url, jsonPayload, {
    { field = 'Content-Type', value = 'application/json' }
}, 'POST', 10)

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
- **JSON** : pas de lib JSON native Lua — lib embarquée `Json.lua` (à la racine du plugin).
  Utiliser `Json.array(t)` pour forcer une table à se sérialiser en tableau JSON (même vide).
- **Pas de `require` standard pour le SDK** : importer les modules SDK avec `import 'LrXxx'`
  (les modules du plugin, eux, s'importent avec `require`)
- **`LrFunctionContext.postAsyncTaskWithContext`** : requis pour tout appel `LrHttp.post` —
  `LrTasks.startAsyncTask` seul suffit pour `LrHttp.get` mais pas pour POST
- **Heartbeat bridge** : `_G.LR_AUTOMATION_BRIDGE_HEARTBEAT` mis à jour à chaque tour de
  boucle (`PollingLoop`). Permet de détecter une boucle morte (contexte tué sans cleanup)
  et de la relancer même si le flag `_G.LR_AUTOMATION_BRIDGE_RUNNING` est resté à `true`.

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
          "WhiteBalance": "Custom",
          "Temperature": 5650,
          "Tint": -5,
          "Exposure2012": 0.35
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
      "catalog_path": "C:/photos sony/catalog/Photos.lrcat",
      "exif": {
        "iso": 800,
        "aperture": 2.8,
        "shutter_speed": "1/200",
        "focal_length": 85,
        "camera": "ILCE-7M4"
      },
      "current_develop": {
        "WhiteBalance": "Custom",
        "Temperature": 5500,
        "Tint": 0,
        "Exposure2012": 0.0,
        "Highlights2012": 0,
        "Shadows2012": 0
      }
    }
  ]
}
```

> `catalog_path` permet à l'App de localiser les bundles `Previews.lrdata` /
> `Smart Previews.lrdata` du catalogue (cf. pipeline image ci-dessus).

---

## Paramètres de développement Lr (noms SDK)

> Liste complète avec plages de valeurs dans [`documentation/lr15_sdk_api_reference.md`](documentation/lr15_sdk_api_reference.md) — section 6.
> Couvre : exposition, WB, HSL, Color Grading, Point Color, Tone Curve, Denoise AI,
> Lens Corrections, calibration caméra, recadrage, effets, ProcessVersion.

Groupes principaux (⚠️ en PV2012 les noms réels portent le suffixe `2012` :
`Exposure2012`, `Contrast2012`, `Highlights2012`… — ce sont ces clés que `getDevelopSettings`
retourne et qu'`applyDevelopSettings` attend) :

| Groupe | Paramètres SDK |
|---|---|
| Exposition | `Exposure2012`, `Contrast2012`, `Highlights2012`, `Shadows2012`, `Whites2012`, `Blacks2012`, `Clarity2012`, `Dehaze` |
| Balance des blancs | `Temperature`, `Tint`, `WhiteBalance` |
| Couleur | `Vibrance`, `Saturation` |
| HSL (8 canaux) | `HueAdjustmentRed/…`, `SaturationAdjustmentRed/…`, `LuminanceAdjustmentRed/…` |
| Color Grading | `ColorGradeShadowHue/Sat/Lum`, `ColorGradeMidtoneHue/Sat/Lum`, `ColorGradeHighlightHue/Sat/Lum` |
| Ton / Courbe | `ParametricShadows`, `ParametricDarks`, `ParametricLights`, `ParametricHighlights` |
| Netteté | `Sharpness`, `SharpenRadius`, `SharpenDetail`, `SharpenEdgeMasking`, `Texture` |
| Bruit | `LuminanceSmoothing`, `ColorNoiseReduction` |
| Denoise AI | ⚠️ noms de paramètres non vérifiés — consulter doc Adobe SDK |
| Calibration | `CameraProfile`, `RedHue/Sat`, `GreenHue/Sat`, `BlueHue/Sat` |

> Le sous-ensemble extrait par `PhotoData.lua` (`DEVELOP_KEYS`, noms PV2012) :
> `WhiteBalance`, `Temperature`, `Tint`, `Exposure2012`, `Contrast2012`,
> `Highlights2012`, `Shadows2012`, `Whites2012`, `Blacks2012`, `Clarity2012`,
> `Dehaze`, `Vibrance`, `Saturation`. `WhiteBalance="Custom"` sert de marqueur de
> seed côté App (`core.seeds.is_seed`). Étendre la liste si un nouvel algo le nécessite.

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
3. Tester les endpoints : `curl http://127.0.0.1:5000/health`
4. Mock du plugin : `python -m app.tools.mock_plugin` pour simuler polling + résultats (données factices, sans Lr)

> **`python -m app.main` tourne 100 % indépendamment de Lightroom.** Lr n'a pas besoin
> d'être ouvert : le serveur FastAPI démarre seul (le pont reste juste « déconnecté »
> tant qu'aucun plugin ne poll). Sert à tester les fonctionnalités App sans Lr :
> - **Cœur image / analyse** (`core/`) : appeler directement les fonctions sur des
>   `.ARW` réels — `raw.load_linear`, `analysis.gray_world_wb`, `wb_model.calibrate`,
>   `seeds.plan_adjustments` — sans passer par le serveur ni le GUI. C'est le chemin
>   le plus rapide pour valider un algo (cf. scripts `tools/`).
> - **Endpoints HTTP** : lancer `app.main` + `app.tools.mock_plugin` → boucle complète
>   job → résultat sans Lr.
> Le décodage RAW n'exige que le fichier `.ARW` sur disque, jamais le catalogue ni Lr.

### Test end-to-end
1. Lancer l'App (via menu Lr "Démarrer / connecter" ou `launch_app.ps1` directement)
2. Vérifier `GET /bridge` → `connected: true` (pont actif), ou l'indicateur live dans le GUI
3. Sélectionner photos dans Lr, cliquer « Analyser la sélection » dans le GUI
4. Vérifier que les métriques s'affichent (luminance Y linéaire + WB gray-world, décodage RAW)
5. **Calibrage WB** : corriger la WB de 5-8 photos (seeds), cliquer « Calibrer WB sur la
   sélection » → vérifier le régime détecté, puis « Appliquer WB au reste »

---

## Backlog

### Plugin Lua — fait
- [x] `Info.lua` — manifeste complet (menus Bibliothèque / Fichier / Aide)
- [x] `MenuConnect.lua` / `MenuRelaunch.lua` / `ShowMessage.lua` — entrées menu
- [x] `HttpClient.lua` — GET/POST JSON via LrHttp
- [x] `PollingLoop.lua` — boucle 300ms, dispatch jobs, heartbeat, garde anti-doublon
- [x] `Actions.lua` — connect, relaunch, checkStatus
- [x] `AppLauncher.lua` — start / stop / relaunch process Python via `launch_app.ps1`
- [x] `PhotoData.lua` — path, EXIF, develop settings, catalog_path
- [x] `Adjustments.lua` — withWriteAccessDo, applyDevelopSettings batch
- [x] `Json.lua` — parser/encodeur JSON embarqué
- [x] `Utils.lua` — logf, chemins projet, test popup
- [x] `PluginInfoProvider.lua` — section custom Gestionnaire modules externes

### App Python — fait
- [x] Setup : `requirements.txt`, venv, structure dossiers
- [x] `server/api.py` — endpoints health, status, bridge, jobs, shutdown
- [x] `server/job_queue.py` — queue thread-safe, heartbeat, résultats
- [x] `server/models.py` — modèles Pydantic (Job, JobResult, PhotoResult, ExifData, PhotoAdjustment)
- [x] `main.py` — FastAPI (thread daemon) + GUI PySide6 (thread principal)
- [x] `core/color.py` — espaces couleur analyse : ProPhoto linéaire, luminance Y, → sRGB display
- [x] `core/raw.py` — décodage ARW : `load_linear` (ProPhoto, analyse) + `load_rgb` (sRGB u8)
- [x] `core/image_source.py` — source RAW → ProPhoto linéaire (`LoadedImage`)
- [x] `core/analysis.py` — métriques exposition (Y) + WB gray-world, en linéaire
- [x] `core/catalog.py` — localisation .lrcat / .lrdata, ouverture SQLite lecture seule
- [x] `core/previews.py` — résolution id_global → preview, aperçu rendu (verif) ; SP = inspection
- [x] `core/wb_model.py` — modèle WB Temp = pente·(r/g) + intercept seeds (validé CGC 64% gain)
- [x] `core/seeds.py` — collecte seeds + `plan_adjustments` (corrections WB/expo des non-seeds)
- [x] `core/regime.py` — détection physique/artistique (ratio résidu/étalement, validé CGC/Yggdrasil)
- [x] `core/raw.py` — `read_asshot_wb` (r/g, b/g du WB boîtier)
- [x] `gui/main_window.py` — check, analyse, **calibrer WB + appliquer**, indicateur pont
- [x] `gui/job_worker.py` — QThread d'attente du plugin
- [x] `gui/analysis_worker.py` — QThread d'analyse pixel (RAW → ProPhoto linéaire)
- [x] `gui/calibrate_worker.py` — QThread : seeds → calibrate → regime → plan_adjustments
- [x] `tools/mock_plugin.py` — mock du plugin pour tests sans Lightroom
- [x] `tools/{analyze_ground_truth,series_audit,validate_wb_seeds}.py` — vérité terrain, audit, validation
- [x] `tools/calibrate_sp_vs_raw.py` — calibration Smart Preview ↔ RAW (a tranché : RAW seul)

> **Méthode WB tranchée par les essais** (CGC 1004, St-Valentin, Yggdrasil) : sur un
> event typique la Temperature suit l'AWB boîtier (pente physique ~2450K/[r/g] ILCE-7M4)
> + un biais chaleur par-event calibré sur 5-8 seeds. Régime artistique (Yggdrasil)
> détecté et basculé en repli. Pas de prédiction depuis stats pixel globales (impasse
> prouvée à n=1142) → `core/prediction.py` supprimé.

### À faire
- [x] Perf : décodage RAW/JPEG **sur GPU** (torch CUDA + nvJPEG) + scheduler VRAM-aware (`core/gpu*.py`) — remplace le chemin LibRaw `postprocess`. Voir « Accélération » du Pipeline image
- [x] Cache SQLite des analyses (`core/cache.py`, 4 tables, clé `uuid`+`hash`) — 2e passage sans décode
- [x] Fix gel : `ProcessPoolExecutor` non borné (32 process) remplacé par pool borné + scheduler GPU
- [ ] Câbler le canal miniature plugin (`get_thumbnails`/`render_probe`) côté `main_window` : actuellement `thumbnail_paths` jamais rempli → l'autocorrect dépend de `Previews.lrdata` seul (cause « mesure rien » si pas d'aperçus)
- [ ] Sélection explicite des seeds dans le GUI (au lieu de l'heuristique WB Custom seule)
- [ ] Repli régime artistique : boucle fermée (rendu Previews.lrdata → cible → nudge) ou marquage manuel
- [ ] GUI : `photo_panel.py` / `analysis_panel.py` — aperçus, histogrammes, outliers WB
- [ ] Test end-to-end réel dans Lightroom (calibrer → appliquer → vérifier)
