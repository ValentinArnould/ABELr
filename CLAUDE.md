# ABELr — Plugin Lightroom Classic

Plugin Lightroom Classic (Lua + SDK Lr) + application Python externe pour retouche batch
intelligente. Cœur : **exposition / HSL / Calibration / White Balance par photo**, calibrée sur des
**seeds** (photos repères marquées à la main) via matching k-NN sur l'analyse RAW zone nette.

**Plugin auto-suffisant** : `ABELr.lrplugin/` embarque tout — le code Lua *et* le package
Python complet (`ABELr.lrplugin/app/`), plus `launch.ps1`/`bootstrap.ps1`. Copier ce seul
dossier sur une autre machine suffit à installer le plugin (Python 3.11+ + internet requis au
1er lancement — `bootstrap.ps1` construit le venv et installe les dépendances, GPU CUDA détecté
automatiquement sinon repli CPU). Le reste du repo (`documentation/`, `PLAN.md`…) est le
dépôt de dev, pas une dépendance runtime du plugin.

## Où lire quoi

| Fichier | Pour |
|---|---|
| [`documentation/ARCHITECTURE.md`](documentation/ARCHITECTURE.md) | **Comment le système marche** : flux, carte des modules (statut live/mort), pipeline image, cache, GPU, communication |
| [`PLAN.md`](PLAN.md) | **Roadmap / statut** : étapes en cours, tests de non-régression, backlog |
| [`documentation/lr15_sdk_api_reference.md`](documentation/lr15_sdk_api_reference.md) | **Tout code Lua** : imports, APIs SDK, paramètres Camera Raw 18, patterns, limitations. Méthodes ⚠️ = non vérifiées, confirmer avant usage |
| [`documentation/project_overview.md`](documentation/project_overview.md) | Vision globale, décisions historiques |
| [`ABELr.lrplugin/app/README.md`](ABELr.lrplugin/app/README.md) | Install / lancement / structure `core/` |

> Avant d'écrire du Lua ou de chercher un nom de paramètre develop : `lr15_sdk_api_reference.md`.
> Avant d'affirmer qu'un module est utilisé : la carte de statut d'ARCHITECTURE.md (§3) —
> plusieurs modules `core/` sont tool-only ou morts.

## Stack (détail : ARCHITECTURE.md § Stack)

| Couche | Techno |
|---|---|
| Plugin | Lua 5.1 + Adobe Lr Classic SDK 12+ |
| Serveur / GUI | Python 3.11+ · FastAPI · PySide6 (même process : serveur en thread daemon, GUI thread principal) |
| Image / GPU | rawpy · numpy · opencv · torch 2.6.0 + torchvision 0.21.0 (cu124, nvJPEG ; **fallback CPU** si pas de GPU CUDA) |
| Analyse | scipy · scikit-learn · `exiftool` (binaire externe, hors pip) |

---

## Contraintes à ne jamais violer

**Lua / SDK :**
- Lua 5.1 : pas de `//`, `goto`, ni `utf8` stdlib.
- Toute écriture catalog/develop dans `catalog:withWriteAccessDo(...)`.
- Tout I/O bloquant dans `LrTasks.startAsyncTask` ; `LrHttp.post` exige `LrFunctionContext.postAsyncTaskWithContext`.
- Chemins Windows via `LrPathUtils` — jamais concaténer `/`.
- Modules SDK : `import 'LrXxx'` ; modules du plugin : `require`.
- Pas de lib JSON native → `Json.lua` embarqué (`Json.array(t)` force un tableau JSON).
- `Collections.lua`, `Metadata.lua`, `PhotoLookup.lua`, `Presets.lua` (Phase 2, câblés dans
  `PollingLoop.lua`) contiennent des méthodes SDK marquées ⚠️ non vérifiées en Lr live dans leur
  propre en-tête — même règle que `lr15_sdk_api_reference.md` : confirmer avant d'étendre/copier
  leur usage.

**App Python :**
- **GPU prioritaire, fallback CPU** (décision utilisateur, plugin doit tourner sans NVIDIA) :
  `app/core/gpu.py` : `device()` renvoie `cuda` si utilisable, sinon `cpu` — **ne lève jamais**.
  Tout le pipeline (`gpu_raw`, `gpu_jpeg`, `render_metrics_gpu`, `gpu_schedule`) route son device
  via cet appel, donc bascule automatiquement ; les workers GUI logguent un avertissement (pas un
  échec) quand ils tournent en CPU. `require_cuda()`/`GpuUnavailable` restent disponibles pour les
  usages qui veulent explicitement exiger CUDA (`tools/calibrate_hsl_response.py`,
  `tools/validate_gpu_vs_libraw.py`, `tests/test_gpu_parity.py`) — ne pas les utiliser comme gate
  par défaut ailleurs. (Politique précédente « GPU-strict, aucun repli CPU » levée — historique
  dans [[lr_gpu_cache_refactor]].)
- **Cache obligatoire** : les workers consultent `cache` (SQLite, `app/core/cache.py`, 5 tables —
  `LightroomPicture`, `SourceRAW`, `InCameraJPEG`, `PreviewJPEG`, `NeutralPreviewJPEG`) d'abord.
  `ANALYSIS_VERSION` salée dans les hash → changer l'algo de mesure = bumper la constante
  (rebuild complet, pas de migration ; ne pas graver sa valeur ici, elle bouge à chaque bump —
  lire `cache.py` si besoin de la valeur courante).
- **`python -m app.main` tourne sans Lightroom** : le serveur démarre seul, le pont reste juste
  « déconnecté ». Le décodage RAW n'exige que le `.ARW` sur disque, jamais le catalogue ni Lr.

**Paramètres develop = PV2012** : les noms réels portent le suffixe `2012` (`Exposure2012`,
`Highlights2012`…). `WhiteBalance='Custom'` requis pour que `Temperature`/`Tint` prennent effet.
`WhiteBalance='Custom'` sert aussi de marqueur historique côté App.

---

## Communication (détail : ARCHITECTURE.md §2 — ⚠️ ce §2 est en retard sur cette section, se fier à celle-ci)

**Plugin = TOUJOURS client HTTP. App = TOUJOURS serveur (`127.0.0.1:5000`).** L'App ne pousse
jamais : elle dépose un job dans `job_queue`, le plugin le récupère en pollant (`GET /jobs/pending`,
300 ms) et renvoie via `POST /jobs/{id}/result`.

Jobs (14 — source de vérité : `JobType` enum `app/server/models.py` + `dispatch()`
`PollingLoop.lua`, garder synchrones à tout ajout) :
- Base : `test`, `get_selected_photos`, `get_catalog_photos`, `get_thumbnails`, `render_probe`, `apply_adjustments`
- Métadonnées : `set_rating`, `set_flag_color`, `set_keywords`
- Collections : `list_collections`, `create_collection`, `add_to_collection`
- Presets : `list_develop_presets`, `apply_develop_preset`

```json
{ "job_id": "uuid", "type": "apply_adjustments",
  "payload": { "adjustments": [ { "photo_id": "...", "develop": {
      "WhiteBalance": "Custom", "Temperature": 5650, "Tint": -5, "Exposure2012": 0.35 } } ] } }
```

**Second canal — MCP (`app/mcp/server.py` + `tools.py`, monté sur `/mcp` dans `app/server/api.py`)** :
expose le `job_queue` ci-dessus comme 15 tools MCP pour Claude Code lui-même (introspection,
lecture, écriture, métadonnées/collections/presets), enregistré dans [`.mcp.json`](.mcp.json)
(serveur `abelr`, `http://127.0.0.1:5000/mcp`). Sert à piloter Lr live pendant le dev sans
écrire de script. Requiert `python -m app.main` lancé ; tools dépendants du bridge timeout
proprement si le plugin Lr n'est pas connecté (pas de crash).

---

## Workflow de développement

**Plugin Lua :** éditer dans `ABELr.lrplugin/` → Lr : *Fichier > Gestionnaire des modules
externes* > Recharger → tester via *Bibliothèque > Modules externes* → logs `Utils.logf` dans
*Aide > Console Lua*.

**App Python :** toutes les commandes se lancent depuis `ABELr.lrplugin/` (le plugin est la
racine du package Python depuis la refonte auto-suffisante — `app/` n'est plus à la racine du
repo). `python -m app.main` (ou `launch.ps1`, qui chaîne `bootstrap.ps1` tout seul si `app/.venv`
est absent — 1er lancement). Venv attendu en `app/.venv` (relatif à `ABELr.lrplugin/`).
Endpoints : `curl http://127.0.0.1:5000/health`. Mock sans Lr : `python -m app.tools.mock_plugin`.
Piloter Lr live sans écrire de script : tools MCP `abelr` (cf. § Communication) — app
lancée requise.

**Tests unitaires (fonctions pures, sans GPU ni RAW) — depuis `ABELr.lrplugin/` :**
```
python -m pytest app/tests -q            # tout
python -m pytest app/tests -q -m "not gpu"   # exclut la parité GPU (skippée si CUDA absent)
```

**Chemin le plus rapide pour valider un algo** : appeler `core/` directement sur des `.ARW` réels
(`raw.load_linear`, `analysis.gray_world_wb`, `gpu_raw.analyze_raw_gpu`, `seed_match.k_nearest`)
sans passer par le serveur ni le GUI — cf. `tools/`.

**Installer sur une autre machine :** copier uniquement le dossier `ABELr.lrplugin/`
(pas besoin du reste du repo) → l'installer comme module externe Lr → menu *Démarrer/connecter
l'application* déclenche `bootstrap.ps1` au 1er lancement (Python 3.11+ doit être sur le PATH,
connexion internet requise le temps du téléchargement — torch CUDA ~2,5 Go si GPU NVIDIA détecté
via `nvidia-smi`, sinon build CPU ~250 Mo). `exiftool` reste à part (binaire externe, PATH système
ou `ABELr.lrplugin/bin/exiftool.exe` si bundlé manuellement — absence non bloquante).

---

## Conventions de nommage

| Contexte | Convention |
|---|---|
| Fichiers Lua | `PascalCase.lua` · fonctions/locales `camelCase` · constantes `UPPER_SNAKE_CASE` |
| Fichiers Python | `snake_case.py` · classes `PascalCase` · fonctions/vars `snake_case` |
| Clés JSON échangées | `snake_case` |
| Noms paramètres SDK Lr dans JSON | `PascalCase` (identique au SDK) |
