# Lr_automation — Plugin Lightroom Classic

Plugin Lightroom Classic (Lua + SDK Lr) + application Python externe pour retouche batch
intelligente. Cœur : **balance des blancs / exposition / HSL par photo**, calibrée sur des
**seeds** (photos repères marquées à la main) via matching k-NN sur l'analyse RAW zone nette.

## Où lire quoi

| Fichier | Pour |
|---|---|
| [`documentation/ARCHITECTURE.md`](documentation/ARCHITECTURE.md) | **Comment le système marche** : flux, carte des modules (statut live/mort), pipeline image, cache, GPU, communication |
| [`PLAN.md`](PLAN.md) | **Roadmap / statut** : étapes en cours, tests de non-régression, backlog |
| [`documentation/lr15_sdk_api_reference.md`](documentation/lr15_sdk_api_reference.md) | **Tout code Lua** : imports, APIs SDK, paramètres Camera Raw 18, patterns, limitations. Méthodes ⚠️ = non vérifiées, confirmer avant usage |
| [`documentation/project_overview.md`](documentation/project_overview.md) | Vision globale, décisions historiques |
| [`app/README.md`](app/README.md) | Install / lancement / structure `core/` |

> Avant d'écrire du Lua ou de chercher un nom de paramètre develop : `lr15_sdk_api_reference.md`.
> Avant d'affirmer qu'un module est utilisé : la carte de statut d'ARCHITECTURE.md (§3) —
> plusieurs modules `core/` sont tool-only ou morts.

## Stack (détail : ARCHITECTURE.md § Stack)

| Couche | Techno |
|---|---|
| Plugin | Lua 5.1 + Adobe Lr Classic SDK 12+ |
| Serveur / GUI | Python 3.11+ · FastAPI · PySide6 (même process : serveur en thread daemon, GUI thread principal) |
| Image / GPU | rawpy · numpy · opencv · torch 2.6.0 + torchvision 0.21.0 (cu124, nvJPEG) |
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

**App Python :**
- **GPU-strict** : aucun repli CPU de calcul. `gpu.require_cuda()` lève `GpuUnavailable` si CUDA
  absent → le worker échoue avec un message clair. Ne pas ajouter de fallback CPU silencieux.
- **Cache obligatoire** : les workers consultent `cache` (SQLite, 5 tables) d'abord. `ANALYSIS_VERSION`
  salée dans les hash → changer l'algo de mesure = bumper la version (rebuild complet, pas de migration).
- **`python -m app.main` tourne sans Lightroom** : le serveur démarre seul, le pont reste juste
  « déconnecté ». Le décodage RAW n'exige que le `.ARW` sur disque, jamais le catalogue ni Lr.

**Paramètres develop = PV2012** : les noms réels portent le suffixe `2012` (`Exposure2012`,
`Highlights2012`…). `WhiteBalance='Custom'` requis pour que `Temperature`/`Tint` prennent effet.
`WhiteBalance='Custom'` sert aussi de marqueur historique côté App.

---

## Communication (détail : ARCHITECTURE.md §2)

**Plugin = TOUJOURS client HTTP. App = TOUJOURS serveur (`127.0.0.1:5000`).** L'App ne pousse
jamais : elle dépose un job dans `job_queue`, le plugin le récupère en pollant (`GET /jobs/pending`,
300 ms) et renvoie via `POST /jobs/{id}/result`. Les ajustements passent aussi par la queue (job
`apply_adjustments`).

Jobs : `test`, `get_selected_photos`, `get_catalog_photos`, `get_thumbnails`, `render_probe`,
`apply_adjustments`.

```json
{ "job_id": "uuid", "type": "apply_adjustments",
  "payload": { "adjustments": [ { "photo_id": "...", "develop": {
      "WhiteBalance": "Custom", "Temperature": 5650, "Tint": -5, "Exposure2012": 0.35 } } ] } }
```

---

## Workflow de développement

**Plugin Lua :** éditer dans `LrAutomation.lrplugin/` → Lr : *Fichier > Gestionnaire des modules
externes* > Recharger → tester via *Bibliothèque > Modules externes* → logs `Utils.logf` dans
*Aide > Console Lua*.

**App Python :** `python -m app.main` depuis la racine (ou `launch_app.ps1`). Venv attendu en
`app/.venv`. Endpoints : `curl http://127.0.0.1:5000/health`. Mock sans Lr :
`python -m app.tools.mock_plugin`.

**Tests unitaires (fonctions pures, sans GPU ni RAW) :**
```
python -m pytest app/tests -q            # tout
python -m pytest app/tests -q -m "not gpu"   # exclut la parité GPU (skippée si CUDA absent)
```

**Chemin le plus rapide pour valider un algo** : appeler `core/` directement sur des `.ARW` réels
(`raw.load_linear`, `analysis.gray_world_wb`, `gpu_raw.analyze_raw_gpu`, `seed_match.k_nearest`)
sans passer par le serveur ni le GUI — cf. `tools/`.

---

## Conventions de nommage

| Contexte | Convention |
|---|---|
| Fichiers Lua | `PascalCase.lua` · fonctions/locales `camelCase` · constantes `UPPER_SNAKE_CASE` |
| Fichiers Python | `snake_case.py` · classes `PascalCase` · fonctions/vars `snake_case` |
| Clés JSON échangées | `snake_case` |
| Noms paramètres SDK Lr dans JSON | `PascalCase` (identique au SDK) |
