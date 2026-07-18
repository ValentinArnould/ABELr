# Architecture — Lr_automation

Comment le système fonctionne. Pour les **règles de travail** de l'agent → [`../CLAUDE.md`](../CLAUDE.md).
Pour la **roadmap / statut** → [`../PLAN.md`](../PLAN.md). Pour l'**API Lua SDK** →
[`lr15_sdk_api_reference.md`](lr15_sdk_api_reference.md).

> État vérifié par revue de code (2026-07-05). La colonne « statut » des modules reflète
> ce que le code fait réellement, pas l'intention.

---

## 1. Vue d'ensemble

Plugin Lightroom Classic (Lua + SDK Lr) + application Python externe. Le plugin est un
**pont** vers Lightroom ; l'App fait tout le calcul (décodage RAW, analyse, planification
des ajustements) et pilote le plugin via une queue de jobs.

Fonction cœur : **balance des blancs / exposition / HSL batch par photo**, calibrée sur des
**seeds** (photos repères retouchées à la main, marquées explicitement) via matching k-NN sur
l'analyse RAW de la zone nette.

```
Utilisateur ──GUI──▶ App Python (serveur FastAPI + GUI, même process)
                         │  crée un job dans job_queue
                         ▼
                    [ job_queue ]  ◀── polling HTTP 300 ms ── Plugin Lua (client)
                         │                                         │ exécute via SDK Lr
                         │  ◀────────── POST /jobs/{id}/result ─────┘
                         ▼
                    App décode RAW (GPU), mesure, planifie ──▶ job apply_adjustments ──▶ plugin applique
```

---

## 2. Communication plugin ↔ App

**Invariant : le plugin est TOUJOURS client HTTP, l'App TOUJOURS serveur** (`127.0.0.1:5000`).
Le plugin ne peut pas exposer de serveur simplement → il *poll*. L'App ne pousse jamais : elle
dépose un job, le plugin le récupère au prochain poll.

GUI et serveur FastAPI vivent dans le **même process** Python : serveur dans un thread daemon,
GUI sur le thread principal Qt. Ils partagent l'instance `job_queue` en mémoire (`Lock` +
`threading.Event`), pas de HTTP entre eux.

### Endpoints FastAPI (`app/server/api.py`)

| Endpoint | Méthode | Rôle |
|---|---|---|
| `/health` | GET | Healthcheck (plugin vérifie au démarrage) |
| `/status` | GET | État App (jobs pending, pont connecté) |
| `/bridge` | GET | Battement de cœur du pont (secondes depuis dernier poll) |
| `/jobs/pending` | GET | Plugin récupère le prochain job (204 si vide) — marque le heartbeat |
| `/jobs/{id}/result` | POST | Plugin soumet le résultat |
| `/shutdown` | POST | Arrêt propre du process (utilisé par « Relancer ») |

### Queue de jobs (`app/server/job_queue.py`)

Singleton `job_queue`, FIFO thread-safe. Cycle : `submit()` → `wait_result(timeout)` côté GUI ;
`GET /jobs/pending` puis `POST result` côté plugin → `done_event.set()` débloque le GUI.
Éviction des orphelins (TTL), garde de saturation (max pending), heartbeat plugin
(`mark_poll()` / `bridge_connected(threshold=5 s)`).

### Types de jobs (App → plugin)

`test`, `get_selected_photos`, `get_catalog_photos`, `get_thumbnails`, `render_probe`,
`apply_adjustments` — dispatchés dans [`PollingLoop.lua`](../LrAutomation.lrplugin/PollingLoop.lua)
(≈ lignes 47-121).

### Côté plugin (`LrAutomation.lrplugin/`, 14 fichiers Lua)

| Fichier | Rôle |
|---|---|
| `Info.lua` | Manifeste (`LrToolkitIdentifier = com.lrautomation.plugin`, SDK 12, menus) |
| `MenuConnect` / `MenuRelaunch` / `ShowMessage` | Entrées de menu |
| `PluginInfoProvider.lua` | Section Gestionnaire de modules (boutons connect/relaunch/status/test) |
| `Actions.lua` | connect / relaunch / checkStatus |
| `AppLauncher.lua` | Start/stop/relaunch du process Python via `launch_app.ps1` |
| `PollingLoop.lua` | Boucle 300 ms, dispatch jobs, heartbeat `_G.LR_AUTOMATION_BRIDGE_HEARTBEAT` (timeout 5 s) |
| `HttpClient.lua` | Wrappers GET/POST JSON (LrHttp) |
| `PhotoData.lua` | Extraction path/EXIF/develop settings/catalog_path (**71 `DEVELOP_KEYS`**) |
| `Adjustments.lua` | `applyDevelopSettings` batch dans `withWriteAccessDo` |
| `Thumbnails.lua` | `requestJpegThumbnail` (`fetch`) + cycle apply/render/restore (`fetchProbe` → `render_probe`) |
| `Json.lua` | JSON encodeur/décodeur embarqué |
| `Utils.lua` | Log, chemins projet |

---

## 3. Carte des modules Python — statut réel

Racine `app/` : `main.py` (FastAPI thread daemon + GUI Qt), `requirements.txt`, `README.md`.
Venv attendu en `app/.venv` (cf. `launch_app.ps1`).

### `core/` — pipeline image & calcul

**Live (chemin d'exécution réel) :**

| Module | Rôle | Entrées principales |
|---|---|---|
| `gpu.py` | Contexte CUDA strict, budget VRAM, pool de streams | `require_cuda()`, `GpuUnavailable` |
| `gpu_raw.py` | Bayer → demosaic + WB + matrice → ProPhoto + stats (GPU) | `analyze_raw_gpu()` |
| `gpu_jpeg.py` | Décodage JPEG GPU (nvJPEG) + extraction flux | `decode_blobs()`, `extract_jpeg_stream()` |
| `gpu_schedule.py` | Scheduler VRAM-aware : unpack unifié (1 ouverture rawpy), double-buffer CPU/GPU, vagues par pipeline (revue Fable 5 G7) | `process_combined_batch()` (+ wrappers `process_raw_batch()`/`process_embedded_batch()`), `analyze_render_blobs()` |
| `analysis.py` | Métriques exposition (Y) + gray-world en linéaire | `ExposureStats`, `ev100()` |
| `render_metrics.py` | Tone L* / neutral a*b* / bandes HSL en CIELAB (numpy, source de vérité) | `tone_stats()`, `neutral_stats()`, `band_stats()` |
| `render_metrics_gpu.py` | Portage torch CUDA de `render_metrics` (constantes importées de la version numpy) | `analyze_rendered_gpu_dual()` |
| `sharpness.py` | Masque « zone nette » (Laplacien, top 25 %) — restreint les histogrammes au sujet | CPU+GPU |
| `seed_match.py` | k-NN sur seeds → cible Temp/Tint/tone/bandes (pondération 1/distance) | `build_seed_pool()`, `target_from_seeds()` |
| `autocorrect.py` | Orchestration expo+WB+HSL par photo (modes seeds/embedded) | `plan()` → `PhotoAdjustment[]` |
| `exposure.py` | ΔEV depuis L* courant → L* cible | (via `autocorrect`) |
| `hsl.py` | Deltas HSL par bande vs cible (saturation = réduction seule) | (via `autocorrect`) |
| `response.py` | Modèle ∂rendu/∂curseur calibré (cache disque) | `load()` |
| `wb_model.py` | Raffinement Temp/Tint post-k-NN (**live**) | `refine_temp_tint()` (appelé `autocorrect.py:554`) |
| `cache.py` | Cache SQLite (5 tables) | voir §5 |
| `embedded_jpeg.py` | JPEG boîtier (cible embedded) + WB as-shot ; importe `raw` | `RawReference` |
| `raw.py` | Décodage ARW via rawpy (**live** via `embedded_jpeg`, + tools) | `load_linear()`, `load_rgb()` |
| `color.py` | Espaces couleur : ProPhoto linéaire, Y, → sRGB | constantes de conversion |
| `catalog.py` | Localise `.lrcat` + bundles `.lrdata`, SQLite lecture seule | résolution catalogue |
| `previews.py` | Résout `id_global` → fichiers preview | `PreviewIndex` |
| `measure.py` | Sélection du canal de rendu (thumbnail frais / preview passif) | résolution chemin rendu |
| `pipeline.py` | Dataclasses `RenderAnalysis` / `RenderAnalysisDual`, helpers (band_map) | — |
| `exif_profile.py` | Profil style créatif Sony via binaire externe `exiftool` (absence → None non bloquant) | `read_capture_profiles()` |

**Tool-only (mort côté app, gardé pour `tools/`) :**
- `image_source.py` (`LoadedImage`) — importé seulement par `tools/{sharp_raw_predict,series_audit,analyze_ground_truth}.py`.
- `regime.py` — détection régime physique/artistique ; importé par `tools/validate_wb_seeds.py`. Le
  chemin live k-NN n'en a pas besoin. (`regime.py` importe `wb_model`.)

**Supprimés / inexistants :** `core/seeds.py`, `core/adjustments.py` (supprimés — `is_seed` vit
maintenant en DB via `cache`, matching via `seed_match`). `core/prediction.py` n'a jamais existé.

### `gui/`

| Module | Statut | Rôle |
|---|---|---|
| `main_window.py` | live | Boutons seeds / analyser / apply par axe / calibrer neutre, indicateur pont |
| `job_worker.py` | live | QThread générique : soumet un job, attend le résultat plugin |
| `autocorrect_worker.py` | live | QThread : RAW+JPEG boîtier+aperçu (zone nette) → cache → `autocorrect.plan` ; mode `analyze_only` |
| `neutral_preview_worker.py` | live | QThread : ancres neutres (`render_probe`) → cache `NeutralPreviewJPEG` |
| `photo_panel.py` / `analysis_panel.py` | **STUB** | Vides, réservés (aperçus / histogrammes) |

> `analysis_worker.py` supprimé (PLAN étape 1, revue Fable 5) — garde de
> non-réapparition : `app/tests/test_no_dead_modules.py`.

### `server/`
`api.py` (routes), `job_queue.py` (queue + heartbeat), `models.py` (Pydantic : `Job`, `JobResult`,
`PhotoResult`, `ThumbnailResult`, `ExifData`, `PhotoAdjustment`, enums `JobType` / `JobStatus`).

---

## 4. Pipeline image — source & espace d'analyse

**Source = RAW d'origine via rawpy** (décision validée par calibration réelle, cf.
`tools/calibrate_sp_vs_raw.py`). rawpy fait un développement complet et cohérent (WB, matrice,
démosaïquage) — seule source donnant des métriques comparables entre photos.

| Décision | Valeur | Raison |
|---|---|---|
| Format | float32 scène-linéaire 0-1 | Pas de gamma (WB/clipping justes), pas d'écrêtage 8-bit |
| Primaires | ProPhoto (gamut large) | sRGB écrête les couleurs saturées → biais jusqu'à ×2 sur les ratios gray-world |
| Luminance | Y de XYZ (ligne ProPhoto→XYZ) | Exacte, indépendante du gamut |
| WB | `use_camera_wb=True` | Sans elle les ratios mesurent le capteur, pas la scène |
| Affichage GUI | uint8 sRGB à la demande | Jamais pour l'analyse |

**Pourquoi pas la Smart Preview** : son DNG est en `PhotometricInterpretation = 34892` (LinearRaw,
avant WB et matrice) ; LibRaw/rawpy ne décode pas ses tuiles JPEG XL ; la calibration donne un
Δexposition incohérent (σ ≈ 0.7-1.3 stop). Les SP sont aussi souvent absentes. `previews.py` /
`catalog.py` restent utiles pour localiser les bundles et décoder l'**aperçu rendu** (vérifier un
résultat), pas pour mesurer.

### GPU-strict

Décision utilisateur : le décodage pixel est sur **GPU** (torch CUDA + nvJPEG), plus d'appel
LibRaw `postprocess`.

| Étape | Où |
|---|---|
| Décompression/unpack ARW → plan bayer 16-bit | **CPU irréductible** (pool borné aux cœurs physiques) |
| Black-level, WB CFA, demosaic, matrice → ProPhoto, stats | **GPU** (`gpu_raw`) |
| Décodage JPEG (aperçu rendu + JPEG boîtier) | **GPU** nvJPEG (`gpu_jpeg`) |
| tone / neutral / bandes (CIELAB) | **GPU** (`render_metrics_gpu`, validé exact vs numpy ≤ 8 M px ; au-delà, quantiles sous-échantillonnés — biais négligeable, cf. REVIEW_FABLE5 C-04) |

Aucun repli CPU de calcul : `gpu.require_cuda()` lève `GpuUnavailable` si CUDA absent, le worker
échoue avec un message clair. VRAM gérée par `gpu_schedule` (vagues dimensionnées au budget).
Parité vérifiée par `tools/validate_gpu_vs_libraw` (exposition Y corr 1.000 ; gray-world corr
0.97-0.9995, petit biais constant absorbé par le calibrage seeds).

---

## 5. Cache SQLite (`core/cache.py`)

`LrAutomation_cache.db` dans le dossier du catalogue actif. `SCHEMA_VERSION = 4`,
`ANALYSIS_VERSION = "v5-style-keys-g2wb"` salée dans les hash (bump = rebuild complet, pas de
migration ligne à ligne ; v5 = revue Fable 5 G1 : clés style complétées + garde cam_mul[G2]). Les workers consultent le cache d'abord → 2ᵉ passage = zéro décode.
C'est le vrai gain sur les séries 500-1000, en plus du GPU.

| Table | Clé hash | Contenu |
|---|---|---|
| `LightroomPicture` | `hash_develop` | path, EXIF, `current_develop`, flag `is_seed`, profils DCP/capture |
| `SourceRAW` | `hash_raw` (taille:mtime:ANALYSIS_VERSION) | WB as-shot, expo global+sharp, gray-world global+sharp, tone/hsl zone nette, ev100 |
| `InCameraJPEG` | `hash_jpeg` (= signature RAW taille:mtime+version — le JPEG vit dans le .ARW) | tone/neutral/hsl global+sharp, deltas RAW↔JPEG précalculés |
| `PreviewJPEG` | `hash_preview` (= signature fichier source + version) | mesures du rendu courant (état-dépendant) |
| `NeutralPreviewJPEG` | `hash_style` | ancre neutre + `wb_asshot_temp/tint` |

`is_seed` : marqué/démarqué en DB (pas de décode pixel). k-NN `seed_match` lit le pool de seeds.

### Mode embedded ancré-neutre

`neutral_preview_worker` fait rendre chaque photo par le plugin (job `render_probe` : WB As Shot +
`Exposure2012=0` + 24 curseurs HSL à 0, style DCP/tons/crop intact), mesure ce rendu neutre (GPU),
et le cache sous `hash_style` (stable si Temp/Tint/HSL bougent, change si tons/clarté changent). Le
delta `JPEG boîtier − rendu neutre` donne des réglages **absolus** (idempotents), sans dépendre du
rendu courant. Garde anti-probe-périmé : `neutral_preview_worker._anchor_suspect` refuse de cacher
une ancre suspecte.

---

## 6. Résolution uuid → preview (vérification / inspection)

Le `uuid` qui nomme les fichiers de preview n'est **pas** celui envoyé par le plugin
(`getRawMetadata('uuid')` = `id_global`). Deux sauts SQLite (`previews.PreviewIndex`) :

```
id_global  ──(.lrcat: Adobe_images)──▶  id_local
id_local   ──(previews.db: ImageCacheEntry)──▶  uuid de cache + digest
```

Le `uuid` de cache nomme les fichiers (`{uuid}-{digest}_{taille}`) et le DNG Smart Preview
(`{uuid}.dng`), sous `{uuid[0]}/{uuid[:4]}`. `.lrcat` et `previews.db` ouverts en lecture seule
immuable (`mode=ro&immutable=1`) : cohabite avec Lightroom ouvert.

---

## 7. Modèle seeds → correction (flux d'un Apply)

1. **Marquer + analyser références** : `AutoCorrectWorker(analyze_only=True)` — RAW (demosaic GPU,
   zone nette) + JPEG boîtier + aperçu rendu → peuple le cache, marque `is_seed`, n'applique rien.
2. **Apply `<axe>`** : `AutoCorrectWorker(axes={axe}, force_fresh_preview=True)` — mesure (aperçu
   toujours redécodé, jamais servi du cache pendant un Apply) → cible = JPEG boîtier (mode embedded)
   ou k-NN sur les seeds les plus proches (`seed_match`, mode seeds) → `autocorrect.plan` → job
   `apply_adjustments`. Le plugin l'applique via `photo:applyDevelopSettings()`.
3. `wb_model.refine_temp_tint` raffine Temp/Tint après le k-NN (mode seeds).

Les cibles sont **absolues** (L*/Temperature visées, pas un delta ajouté) → convergence : un 2ᵉ
Apply sur un aperçu à jour donne un delta ≈ 0.

---

## 8. Limitations connues

- **Verrou `requestJpegThumbnail`** : `Thumbnails.fetch` suppose que le thumbnail reflète l'état
  develop courant, pas un cache périmé. `force_fresh_preview=True` bypasse le cache SQLite de l'App,
  mais si **Lightroom** n'a pas régénéré `Previews.lrdata` entre deux clics, la mesure reste
  périmée. Repli prévu non câblé : canal `RenderChannel.EXPORT` (`Thumbnails.fetchProbeExport` +
  job `render_probe_export`). À valider en conditions réelles (convergence 2ᵉ Aperçu ≈ 0). Cf.
  PLAN.md étape 8.
- Panneaux GUI (`photo_panel`, `analysis_panel`) au stade stub.

---

## Stack

| Couche | Technologie |
|---|---|
| Plugin Lr | Lua 5.1 + Adobe Lr Classic SDK 12+ |
| Serveur | Python 3.11+ + FastAPI + uvicorn |
| GUI | PySide6 (Qt6) |
| Image / RAW | rawpy + numpy + opencv |
| GPU | torch 2.6.0 + torchvision 0.21.0 (cu124) — nvJPEG |
| Previews | tifffile + imagecodecs (libjxl) + SQLite |
| Analyse | scipy + scikit-learn |
| Profil boîtier | binaire externe `exiftool` (hors pip) |

> Le décodage RAW (LibRaw/rawpy), la décompression JPEG XL et OpenCV sont déjà du C/C++ : pas de
> gain Rust sur ces parties. Profiler (`py-spy`, `cProfile`) avant d'envisager PyO3.
