# Revue complète — Fable 5

> **But** : audit 4 axes — bugs · architecture/code mort · perf · doc/roadmap.
> **Modèle** : `claude-fable-5` · **Méthode** : 4 passes séquentielles (voir Journal).
> **Règle d'or** : aucun finding sans `file:line`. Noms de paramètres develop vérifiés
> contre [`lr15_sdk_api_reference.md`](lr15_sdk_api_reference.md). Méthodes ⚠️ du SDK = PLAUSIBLE au mieux.

## Légende

**Sévérité** : 🔴 BLOQUANT (casse / corruption / faux résultat) · 🟠 MAJEUR (bug réel, chemin non-nominal) · 🟡 MINEUR (robustesse, edge case) · ⚪ NIT (style, lisibilité)
**Statut** : `CONFIRMÉ` (reproduit / prouvé par le code lu) · `PLAUSIBLE` (raisonnement solide, non prouvé)
**Effort fix** : S (< 30 min) · M (< 1/2 j) · L (≥ 1/2 j)

---

## Passe 0 — Vérité terrain (archi / doc)

> Confronter le code réel à ARCHITECTURE.md §3 (carte des modules) + PLAN.md. Établit le
> périmètre vivant/mort AVANT toute chasse aux bugs. Un module mort ne se corrige pas, il se documente.

### 0.1 Corrections carte des modules (§3 ARCHITECTURE.md)

**Verdict : ZÉRO correction de statut.** La carte §3 est exacte sur les 25 modules `core/`
(23 live + 2 tool-only), les 7 `gui/`, les 3 `server/` et les 14 Lua. Inventaire complet
ci-dessous (audit exhaustif, 2026-07-17). Nuances de chaîne en fin de section.

**`core/` — live direct** (importé par `gui/`/`server/`/`main.py`) :

| Module | Statut doc | Statut réel | Preuve (référence entrante) |
|---|---|---|---|
| `analysis.py` | live | live | `gui/autocorrect_worker.py:31` |
| `autocorrect.py` | live | live | `gui/autocorrect_worker.py:32,34` |
| `cache.py` | live | live | `gui/main_window.py:44`, `gui/autocorrect_worker.py:32`, `gui/neutral_preview_worker.py:37` |
| `exif_profile.py` | live | live | import `gui/autocorrect_worker.py:32`, appel `:143` |
| `gpu.py` | live | live | `gui/autocorrect_worker.py:32`, `gui/neutral_preview_worker.py:37` |
| `gpu_jpeg.py` | live | live | `gui/autocorrect_worker.py:32`, `gui/neutral_preview_worker.py:37` |
| `gpu_schedule.py` | live | live | `gui/autocorrect_worker.py:32` |
| `measure.py` | live | live | `gui/autocorrect_worker.py:32` |
| `previews.py` | live | live | `gui/autocorrect_worker.py:35` |
| `render_metrics.py` | live | live | `gui/neutral_preview_worker.py:37` |
| `render_metrics_gpu.py` | live | live | `gui/neutral_preview_worker.py:37` |
| `response.py` | live | live | import `gui/autocorrect_worker.py:33`, appel `load()` `:290` |
| `seed_match.py` | live | live | `gui/autocorrect_worker.py:33` |

**`core/` — live par chaîne** (importeur direct = autre module core live) :

| Module | Statut doc | Statut réel | Preuve (chaîne entrante) |
|---|---|---|---|
| `catalog.py` | live | live (chaîne) | `core/previews.py:35` ← previews live ; + 8 tools |
| `color.py` | live | live (chaîne) | `core/gpu_raw.py:27`, `core/raw.py:23`, `core/analysis.py:23` |
| `embedded_jpeg.py` | live | live (chaîne) | `core/gpu_schedule.py:25-26` |
| `exposure.py` | live | live (chaîne) | `core/autocorrect.py:37` |
| `gpu_raw.py` | live | live (chaîne) | `core/gpu_schedule.py:25,27`, appels `:73,:75` |
| `hsl.py` | live | live (chaîne) | `core/autocorrect.py:36` |
| `pipeline.py` | live | live (chaîne) | `core/cache.py:44`, `core/autocorrect.py:41`, `core/embedded_jpeg.py:21`, `core/gpu_jpeg.py:22`, `core/gpu_schedule.py:28`, `core/render_metrics_gpu.py:19` |
| `raw.py` | live | live (chaîne) | `core/embedded_jpeg.py:20` ; + tools (conforme doc « live via embedded_jpeg, + tools ») |
| `sharpness.py` | live | live (chaîne) | `core/gpu_raw.py:27`, `core/pipeline.py:20`, `core/render_metrics_gpu.py:212,230` |
| `wb_model.py` | live | live (chaîne) | `core/autocorrect.py:39`, appel `refine_temp_tint` `core/autocorrect.py:554` (= ligne annoncée par la doc) |

**`core/` — tool-only** (aucun importeur hors `app/tools/`) :

| Module | Statut doc | Statut réel | Preuve |
|---|---|---|---|
| `image_source.py` | tool-only | tool-only | `tools/analyze_ground_truth.py:43`, `tools/series_audit.py:37`, `tools/sharp_raw_predict.py:33` — exactement les 3 tools cités par la doc |
| `regime.py` | tool-only | tool-only | `tools/validate_wb_seeds.py:21` seul entrant ; importe bien `wb_model` (`core/regime.py:24`) comme dit |

**`gui/` :**

| Module | Statut doc | Statut réel | Preuve |
|---|---|---|---|
| `main_window.py` | live | live | importé `app/main.py:62`, instancié `:65` |
| `job_worker.py` | live | live | instancié `gui/main_window.py:242,268,320,379,585` |
| `autocorrect_worker.py` | live | live | instancié `gui/main_window.py:411,426` |
| `neutral_preview_worker.py` | live | live | instancié `gui/main_window.py:345` |
| `analysis_worker.py` | MORT | MORT confirmé | classe définie `gui/analysis_worker.py:39`, zéro instanciation/import dans tout `app/` (grep `AnalysisWorker`) |
| `photo_panel.py` | STUB | STUB confirmé | 13 lignes, classe `:11`, `__init__` vide, jamais importé |
| `analysis_panel.py` | STUB | STUB confirmé | 13 lignes, classe `:11`, `__init__` vide, jamais importé |

**`server/` :**

| Module | Statut doc | Statut réel | Preuve |
|---|---|---|---|
| `api.py` | live | live | `app/main.py:35` (uvicorn thread daemon `:56-57`) |
| `job_queue.py` | live | live | singleton `job_queue.py:168` ← `api.py:14`, `main_window.py:45`, `job_worker.py:13`, `neutral_preview_worker.py:38` |
| `models.py` | live | live | `api.py:15`, `job_queue.py:17`, `main_window.py:46`, `job_worker.py:14`, `neutral_preview_worker.py:39`, `autocorrect_worker.py:36` |

**Lua (14 fichiers) — tous vivants**, aucun orphelin :

| Fichier | Preuve entrante |
|---|---|
| `Info.lua` | manifeste, chargé par Lr (racine du graphe) |
| `MenuConnect.lua` / `MenuRelaunch.lua` / `ShowMessage.lua` | `Info.lua:22,26,30` (+ menus Export `:38-46`, Help `:54-62`) |
| `PluginInfoProvider.lua` | `Info.lua:16` |
| `Actions.lua` | `MenuConnect.lua:19`, `MenuRelaunch.lua:14`, `PluginInfoProvider.lua:13` |
| `AppLauncher.lua` | `Actions.lua:11` |
| `PollingLoop.lua` | `Actions.lua:12` |
| `HttpClient.lua` | `Actions.lua:13`, `AppLauncher.lua:14`, `PollingLoop.lua:19` |
| `PhotoData.lua` | `PollingLoop.lua:20` |
| `Adjustments.lua` | `PollingLoop.lua:21` |
| `Thumbnails.lua` | `PollingLoop.lua:22` |
| `Json.lua` | `HttpClient.lua:9`, `PhotoData.lua:9`, `PollingLoop.lua:23` |
| `Utils.lua` | 7 entrants (`Adjustments.lua:14`, `AppLauncher.lua:15`, `HttpClient.lua:10`, `PluginInfoProvider.lua:14`, `PollingLoop.lua:24`, `ShowMessage.lua:6`, `Thumbnails.lua:15`) |

**Nuance à retenir pour PLAN.md étape 1** : le seul importeur GUI *direct* de `gpu_raw`
et `raw` est le module mort `analysis_worker.py:19` — leur statut live tient uniquement
par la chaîne `gpu_schedule`/`embedded_jpeg`. Supprimer `analysis_worker` (étape 1) ne
tue donc aucun module core, mais fera de `gpu_schedule` l'unique entrant de `gpu_raw`.

### 0.2 Divergences doc ↔ code (hors carte modules)

| ID | Doc (fichier:section) | Affirme | Code réel (file:line) | Écart |
|---|---|---|---|---|
| D-01 | ARCHITECTURE.md:80 (§2, table plugin) | `PhotoData.lua` : « **42 `DEVELOP_KEYS`** » | `PhotoData.lua:21` : table de **44** entrées | Compte faux (44, pas 42) |
| D-02 | ARCHITECTURE.md:143-144 (§3 `server/`) | `models.py` = « `Job`, `JobResult`, `PhotoResult`, `ExifData`, `PhotoAdjustment`, enum `JobType` » | s'y ajoutent `JobStatus` (`models.py:26`, utilisé `job_queue.py:17`) et `ThumbnailResult` (`models.py:65`, utilisé `neutral_preview_worker.py:39`) | Liste incomplète (2 types utilisés omis) |
| D-03 | ARCHITECTURE.md:66-67 (§2 jobs) | dispatch `PollingLoop.lua` « ≈ lignes 43-149 » | dispatch réel lignes 47-121 (`PollingLoop.lua:47,55,61,67,96,121`) | Fourchette périmée (mineur, « ≈ » assumé) |
| D-04 | docstring `core/analysis.py:12` | renvoie vers « `core.wb_model` / `core.seeds` » | `core/seeds.py` supprimé (confirmé ARCHITECTURE.md:128-129 ; fichier absent du disque) | Doc embarquée dans le code pointe un module supprimé |
| D-05 | docstring `core/seed_match.py:1` | « remplace `wb_model.py`/`regime.py` côté app live » | `wb_model` est toujours live : `core/autocorrect.py:39`, appel `refine_temp_tint` `:554` | Demi-vrai : `regime` oui (tool-only), `wb_model` non (toujours dans le chemin live) |

**Claims vérifiés CONFORMES** (aucun écart trouvé) :
- Endpoints FastAPI : les 6 annoncés (ARCHITECTURE.md:47-54, CLAUDE.md) = exactement ceux du code — `api.py:22` `/health`, `:28` `/status`, `:39` `/bridge`, `:52` `/jobs/pending`, `:63` `/shutdown`, `:74` `/jobs/{job_id}/result`. Ni extra ni manquant.
- Types de jobs : 6/6 identiques doc↔`models.py:18-23`↔dispatch `PollingLoop.lua:47-121`.
- Cache : 5 tables (`cache.py:132,150,175,193,206` = `LightroomPicture`, `SourceRAW`, `InCameraJPEG`, `PreviewJPEG`, `NeutralPreviewJPEG`), `SCHEMA_VERSION=4` (`cache.py:51`), `ANALYSIS_VERSION="v4-neutral-anchor"` (`cache.py:56`), fichier `ABELr_cache.db` (`cache.py:47`).
- Queue : `submit` `job_queue.py:72`, `wait_result` `:92`, `mark_poll` `:112`, `bridge_connected(threshold=5.0)` `:124`, TTL orphelins 900 s `:38`, garde saturation 100 `:41` — conforme ARCHITECTURE.md:56-61 et §2 (seuil 5 s).
- GPU-strict : `GpuUnavailable` `gpu.py:25`, `require_cuda()` `gpu.py:50` ; torch 2.6.0 / torchvision 0.21.0 épinglés `requirements.txt:21-22`.
- Poll 300 ms (`PollingLoop.lua:28` `POLL_INTERVAL = 0.3`) + heartbeat `_G.ABELR_BRIDGE_HEARTBEAT` (`PollingLoop.lua:38-39`) — conforme.
- `Thumbnails.lua` : `fetch` `:46`, `fetchProbe` `:127` ; `fetchProbeExport` **inexistant** — conforme à ARCHITECTURE.md §8 / PLAN.md étape 8 qui le disent « prévu non câblé ».
- Chemins HTTP côté plugin : `/health` (`HttpClient.lua:53`, `AppLauncher.lua:63`), `/shutdown` (`AppLauncher.lua:65`), `/jobs/pending` (`PollingLoop.lua:152`), `/jobs/{id}/result` (`PollingLoop.lua:188`). `/status` et `/bridge` ne sont pas appelés par le plugin (endpoints d'inspection côté App) — cohérent avec leur rôle doc.
- `main.py` : FastAPI en thread daemon (`main.py:56-57`, uvicorn `127.0.0.1:5000` `:39`), GUI Qt thread principal (`:64-67`) — conforme §2.
- PLAN.md étape 1 (« `AnalysisWorker` jamais instancié ni importé — vérifié ») : re-confirmé.
- Modules « supprimés / inexistants » (ARCHITECTURE.md:128-129) : `core/seeds.py`, `core/adjustments.py`, `core/prediction.py` absents du disque — confirmé (glob `app/core/*.py`).

---

## Passe 1 — Bugs par sous-système

### (a) Plugin Lua — `ABELr.lrplugin/` (14 fichiers)

Conformité vérifiée (aucun écart) : écritures develop toutes dans `withWriteAccessDo`
(`Adjustments.lua:54`, `Thumbnails.lua:154,178`) ; `LrHttp.post` uniquement sous
`postAsyncTaskWithContext` (`PollingLoop.lua:210`, `Actions.lua:18`) ; chemins via
`LrPathUtils` partout ; `import`/`require` corrects ; `Json.array` posé sur tous les
tableaux sortants ; PV2012 respecté (`Exposure2012`…, `WhiteBalance='Custom'` écrit avec
chaque Temperature/Tint côté App — `autocorrect.py:453,556`).

| ID | file:line | Sév | Statut | Problème | Fix | Effort |
|---|---|---|---|---|---|---|
| L-01 | `Thumbnails.lua:63` | 🟠 | PLAUSIBLE | Valeur de retour de `requestJpegThumbnail` jetée. Gotcha SDK connu (non couvert par la réf locale) : l'objet requête collecté par le GC peut faire que le callback ne tire **jamais** → timeouts intermittents « pas de JPEG retourné » | Retenir les retours dans une table locale vivante jusqu'à la fin de la boucle d'attente | S |
| L-02 | `Thumbnails.lua:59,85-97` | 🟠 | PLAUSIBLE | Fichier de sortie fixe `{photo_id}.jpg` + callbacks tardifs : après un timeout, le callback du job N peut encore écrire et **écraser** le fichier frais du job N+1 (ou muter `results` après retour) → l'App mesure des pixels périmés (probe ≠ état mesuré) | Nom de fichier unique par appel (compteur/nonce) + jeton de génération testé dans le callback | S |
| L-03 | `Thumbnails.lua:178-185` | 🟠 | CONFIRMÉ | Restore du probe : le résultat de `LrTasks.pcall` est ignoré. Si le restore échoue, la photo reste en état neutre (WB As Shot / Exp 0 / HSL 0) **sans aucun signal** dans le résultat du job | Collecter les erreurs de restore, les remonter dans le résultat (`error` par photo_id), logguer | S |
| L-04 | `PollingLoop.lua:125-131` | 🟡 | CONFIRMÉ | Apply partiel (applied>0 avec des erreurs) → `status='ok'`, textes d'erreur perdus (seuls applied/matched/total passent) ; le GUI affiche « Appliqué : n/m » sans cause pour les échecs | Joindre un résumé de `report.errors` au résultat même quand status='ok' | S |
| L-05 | `PollingLoop.lua:219-225` | 🟡 | CONFIRMÉ | Heartbeat écrit une fois par tour, **avant** le dispatch : un job long (thumbnails/probe/apply, 1-3 min) rend `bridgeAlive()` faux et `/bridge` déconnecté pendant le travail → GUI « Pont inactif » + `_require_bridge` bloque, alors que le pont travaille | Rafraîchir `_G.ABELR_BRIDGE_HEARTBEAT` dans la boucle d'attente de `Thumbnails.fetch` et la boucle d'apply | S |
| L-06 | `HttpClient.lua:30` + `PollingLoop.lua:156-158` | 🟡 | CONFIRMÉ | Body 200 non-JSON → `Json.decode` nil → `pollOnce` le traite comme « pas de job » : le job reste IN_PROGRESS côté App jusqu'au TTL 900 s, **aucun log** | Logguer rawBody quand status=200 et décodage nil ; distinguer 204 de « décodage raté » | S |
| L-07 | `PollingLoop.lua:188-189` | 🟡 | CONFIRMÉ | POST du résultat non réessayé (status nil = perte réseau) : le job a été **exécuté** (apply compris) mais le worker App timeout — invisible côté Lr | 1-2 retries avec backoff sur `postJsonRaw`, log en échec final | S |
| L-08 | `Json.lua:142-159` | ⚪ | CONFIRMÉ | Décodage `\u` sans paires surrogates (astral → 2×3 octets faux). Starlette envoie de l'UTF-8 brut (ensure_ascii=False) → chemin quasi jamais pris | Gérer D800-DBFF (paire → code point → UTF-8 4 octets) | S |
| L-09 | `Adjustments.lua:30-34` | ⚪ | CONFIRMÉ | Apply matche uniquement la sélection courante (v1 documenté) alors que `Thumbnails.fetchProbe:143-146` a un repli `findPhotoByUuid` : si la sélection change pendant la mesure, photos silencieusement sautées | Même repli `catalog:findPhotoByUuid` que fetchProbe | S |

### (b) Pont HTTP + serveur — `app/server/` + `HttpClient.lua` / `PollingLoop.lua`

Conformité vérifiée : cycle de vie du polling par **génération** (`PollingLoop.lua:206-238`,
aucun flag booléen partagé — conforme au principe mémoire) ; contrat
job_id/type/payload aligné des deux côtés (`models.py:35-41` ↔ `PollingLoop.lua:44-45`) ;
`submit_result` publie état+event **sous le lock** (`job_queue.py:143-152`) ;
`next_pending` saute proprement les entrées évincées (`job_queue.py:129-139`).

| ID | file:line | Sév | Statut | Problème | Fix | Effort |
|---|---|---|---|---|---|---|
| B-01 | `gui/main_window.py:565-570` | 🟠 | CONFIRMÉ | `_pending_ids` (rempli `:546`) n'est **jamais comparé** : « Appliquer » rejoue le plan d'Aperçu même si la sélection Lr a changé entre-temps → apply partiel/incohérent (le plugin n'applique que l'intersection, sans avertir de l'écart) | Re-fetch la sélection et comparer à `_pending_ids` avant `_submit_apply` ; sinon re-planifier | S |
| B-02 | `server/api.py:56-60` | 🟡 | CONFIRMÉ | `next_pending()` pope le job (IN_PROGRESS) **avant** `model_dump(mode="json")` : une valeur de payload non sérialisable → 500 ET job jamais livré (perdu jusqu'au TTL). Payloads actuels sains (floats purs, vérifié `exposure.py:80-86`, `autocorrect.py:453-476`), mais motif fragile | Sérialiser dans un try/except ; en échec, marquer FAILED + libérer l'event au lieu de perdre le job | S |
| B-03 | `gui/neutral_preview_worker.py:104-118` | 🟡 | CONFIRMÉ | `_anchor_suspect` retourne False sur **toute** exception (lecture cache `:112-114` comprise) → une ancre réellement suspecte est alors cachée, ce qui contredit « une ancre suspecte n'est JAMAIS cachée » (l'ancre empoisonne le mode embedded jusqu'au changement de style) | Ne pas avaler l'exception : logguer et traiter comme suspect (ou propager) | S |
| B-04 | `gui/autocorrect_worker.py:166-314` | 🟡 | CONFIRMÉ | `conn` SQLite fermée seulement sur les chemins de succès (`:182`, `:270`, `:282`) ; toute exception (dont `ensure_neutral_previews` RuntimeError) sort par `except :313` **sans close** → fuite de handle par échec | `try/finally` englobant pour `conn` (comme `neutral_preview_worker.py:262-267`) | S |
| B-05 | `gui/main_window.py:585` + `Adjustments.lua:54-76` | 🟡 | PLAUSIBLE | Apply = UNE transaction `withWriteAccessDo` pour toute la sélection, timeout GUI 180 s : à 500+ photos le dépassement est plausible → GUI « Timeout » alors que le plugin applique encore (re-clic = double apply) | Chunker `apply_adjustments` (mêmes lots que render_probe) ou timeout ∝ n | M |
| B-06 | `server/api.py:58-59` | ⚪ | PLAUSIBLE | 204 renvoyé avec body `{}` — RFC : 204 sans body ; passe avec uvicorn[standard]/httptools (prouvé en prod), casserait sur repli h11 | `return Response(status_code=204)` | S |

### (c) Core image / GPU — `raw`, `gpu*`, `pipeline`, `image_source`, `color`, `render_metrics*`

`image_source.py` exclu (tool-only, Passe 0). Conformité vérifiée : `gpu.require_cuda()`
présent à chaque point d'entrée (`gpu_schedule.py:64,90,135`, `gpu_jpeg.py:52`,
workers `:157/:237`) — GPU-strict respecté, aucun repli CPU ; matrices couleur
CPU↔GPU partagées (`render_metrics_gpu.py:31-43` importe celles de `render_metrics`,
`gpu_raw.py:39-42` celles de `color`) ; luminance = ligne Y ProPhoto→XYZ(D50) identique
(`color.py:34` ↔ `gpu_raw.py:182`) ; parité formule Lab/HSV/masque net ligne à ligne OK ;
mémoire GPU libérée par vague (`gpu_schedule.py:79,121,148` `empty_cache`) ; chemins RAW
Windows passés en str à rawpy sans concat manuelle.

| ID | file:line | Sév | Statut | Problème | Fix | Effort |
|---|---|---|---|---|---|---|
| C-01 | `core/gpu_raw.py:159-165` | 🟠 | PLAUSIBLE | WB par site CFA sans garde `wb[3]==0` : dcraw/LibRaw traitent `cam_mul[G2]=0` comme `=G1` ; ici les sites G2 seraient multipliés par 0 → canal vert faussé (demosaic moyenne des zéros). Sony ARW renvoie G2=G1 (d'où la parité validée), mais tout boîtier à cam_mul[3]=0 casse | `if wb_arr[3]==0: wb_arr[3]=wb_arr[1]` — **toucherait les mesures ⇒ bump `ANALYSIS_VERSION`** (no-op sur Sony, sûr) | S |
| C-02 | `core/gpu.py:29-42` | 🟡 | CONFIRMÉ | `_diagnose` mémoïsé `lru_cache` : un échec d'init CUDA **transitoire** (OOM au lancement, driver occupé) est mémorisé → `require_cuda` échoue jusqu'au redémarrage du process alors que le GPU est revenu | Ne mémoïser que le succès (ou invalider sur échec) | S |
| C-03 | `core/raw.py:96` | 🟡 | CONFIRMÉ | `cv2.imdecode(...)[:, :, ::-1]` sans test None : JPEG embarqué corrompu → TypeError. Chaîne live protégée par le try de `embedded_jpeg.load_embedded_rgb:31-34`, mais tout appel direct (tools) crashe | Tester None avant le slicing | S |
| C-04 | `core/render_metrics_gpu.py:66-72` + `core/sharpness.py:63-67` | ⚪ | CONFIRMÉ | Quantiles GPU sous-échantillonnés au-delà de 8M px (borne `torch.quantile`) : parité « exacte vs numpy » non garantie sur RAW 24MP pleine résolution — biais négligeable et documenté en commentaire, mais l'affirmation d'ARCHITECTURE §4 est légèrement trop forte | Rien (ou préciser la doc) | – |
| C-05 | `core/gpu_jpeg.py:34-37` | ⚪ | CONFIRMÉ | `extract_jpeg_stream` prend le **premier** SOI du buffer : sur un conteneur multi-flux ça donnerait le petit niveau. Sans effet en pratique : `previews.find_rendered_preview:60-70` choisit déjà le fichier de niveau max, le `.lrfprev` (petit niveau) n'est qu'un repli documenté | Rien (commentaire) | – |

### (d) Cache SQLite — `cache.py` + hash `measure.py` / `exif_profile.py`

> Préfixe `DB-` (le préfixe `D-` est déjà pris par les divergences doc de la Passe 0).

Conformité vérifiée : 5 tables présentes et alignées lecture/écriture
(`cache.py:132-219`, clés get/put cohérentes par table) ; `ANALYSIS_VERSION` salée dans
`raw_signature:240`, `blob_hash:247`, `style_hash:265` ; `_ensure_schema` DROP+recreate
sur `user_version` ≠ 4 (`cache.py:118-126`) ; UPSERT `put_picture` préserve `is_seed`
(`cache.py:331-340`) ; commit après chaque écriture ; une connexion par worker
(WAL, `check_same_thread=False`).

| ID | file:line | Sév | Statut | Problème | Fix | Effort |
|---|---|---|---|---|---|---|
| DB-01 | `core/cache.py:68-78` ↔ `PhotoData.lua:21-40` | 🟠 | CONFIRMÉ | `_STYLE_KEYS` inclut 14 clés `ColorGrade*` que `DEVELOP_KEYS` (Lua) **n'extrait jamais** → jamais dans `current_develop` → `hash_style` insensible au Color Grading. Idem `Texture`, ToneCurve, Parametric* absents des DEUX côtés. Conséquence : changer Color Grading / courbe / Texture ne recalcule PAS l'ancre neutre → ancre périmée servie du cache → corrections embedded fausses, silencieusement (contredit ARCHITECTURE §5 « change si tons/clarté changent ») | Ajouter ces clés à `DEVELOP_KEYS` (Lua) et compléter `_STYLE_KEYS` ; **bump `ANALYSIS_VERSION`** (sinon les ancres périmées restent valides, les clés étant absentes des anciens snapshots) | M |
| DB-02 | `gui/autocorrect_worker.py:384-386` ↔ `cache.py:14-15,245-247` | 🟡 | CONFIRMÉ | `hash_jpeg` (InCameraJPEG) et `hash_preview` (PreviewJPEG) = `raw_signature` (taille:mtime), **pas** « sha1 des octets » comme l'affirment l'en-tête de cache.py et ARCHITECTURE §5. Cohérent get/put donc pas de bug de fraîcheur, mais `blob_hash` est du code mort et la doc décrit un mécanisme qui n'existe pas | Corriger la doc (ou basculer réellement sur `blob_hash`) ; supprimer/brancher `blob_hash` | S |
| DB-03 | `gui/main_window.py:445-459` | 🟡 | CONFIRMÉ | `_apply_seed_flag` tourne sur le **thread Qt principal** : `put_picture`+`set_seed` par photo = 2 commits synchrones × n (300 photos ≈ 600 commits) → gel du GUI plusieurs secondes (violation de la règle « wait/IO hors thread Qt » appliquée partout ailleurs) | Une transaction unique (executemany + commit final) ou déplacer dans un worker | S |
| DB-04 | `core/cache.py:239-242` | ⚪ | CONFIRMÉ | Repli `"0:0"` (fichier absent) non salé `ANALYSIS_VERSION` — jamais écrit en base (le décodage échoue avant tout put) donc collision théorique seulement | Saler aussi le repli | S |
| DB-05 | `core/cache.py:395-397,404-406` | ⚪ | CONFIRMÉ | `ORDER BY cached_at DESC LIMIT 1` sur des tables à `uuid` PRIMARY KEY (1 ligne max par uuid) — code mort trompeur (suggère un historique qui n'existe pas) | Simplifier en SELECT simple | S |
| DB-06 | `core/cache.py:687-727` | ⚪ | CONFIRMÉ | `get_bias_pool` n'a **aucun appelant** (le worker passe toujours `bias_pools=None`, `autocorrect._build_bias_by_group:272` non plus n'est jamais appelé) — cohérent avec la décision « biais ignoré » de `_plan_embedded:353`, mais c'est du live-doc mort | À traiter en passe archi (mort ou à câbler) | – |

### (e) Analyse / seed-match — `analysis`, `measure`, `seed_match`, `wb_model`, `exposure`, `hsl`, `autocorrect`

`regime.py` exclu (tool-only, Passe 0). Conformité vérifiée : saturation HSL =
**réduction seule** (`hsl.py:102-121`, delta clampé ≤ 0) ; divisions protégées
(`hsl.py:118,127,136` gains ~1e-9, `seed_match.py:158-161` poids, `wb_model` bornes,
`response.py:74-83` pente ≥ 1) ; expo bien en espace rendu L* avec cible absolue et
ancre Exposure2012=0 en embedded (`autocorrect.py:392-419`) ; k-NN : exclusion du
target, z-score par feature, pondération 1/distance (`seed_match.py:98-144`) ;
`refine_temp_tint` borné et jamais de gray-world global (`wb_model.py:119-149`).

| ID | file:line | Sév | Statut | Problème | Fix | Effort |
|---|---|---|---|---|---|---|
| A-01 | `core/exif_profile.py:83-88` | 🟠 | CONFIRMÉ | Lot passé **en argv** : à 500-1000 chemins (~80 car. chacun) la limite Windows CreateProcess (32 767 car.) est dépassée dès ~300 photos → OSError → warning trompeur « exiftool introuvable » et **tout le lot sans profil** (matching k-NN et groupes de biais dégradés). Le docstring promet `-@ argfile`, jamais implémenté | Écrire un argfile temp et appeler `exiftool -@ file` (+ `-charset filename=UTF8`) | S |
| A-02 | `core/exif_profile.py:83-86` | 🟡 | PLAUSIBLE | `text=True` sans `encoding` : stdout exiftool (UTF-8) décodé en cp1252 → chemins accentués (contexte FR) mojibake → `_match_path` échoue → profils None silencieux | `encoding="utf-8"` + options charset exiftool | S |
| A-03 | `core/analysis.py:57-76` | 🟡 | PLAUSIBLE | `parse_shutter_seconds` : Lr FR formate les poses lentes avec **virgule** (« 0,4 s ») → `float()` ValueError → `ev100=None` pour ces photos (contexte scène perdu, champ diagnostic) | Normaliser `,` → `.` avant parsing | S |
| A-04 | `core/autocorrect.py:554` | 🟡 | PLAUSIBLE | `refine_temp_tint(…, m.analysis.neutral, …)` sans garde : une `RenderAnalysis` de cache peut avoir `neutral=None` (`cache._analysis_from_row:295-302` l'autorise) → AttributeError → **tout le run** échoue via le garde-fou worker | Garde `m.analysis.neutral is not None` (sinon skip raffinement) | S |
| A-05 | `core/response.py:173-185` | 🟡 | CONFIRMÉ | `load()` : JSON du cache disque corrompu/tronqué → exception non gérée → toute l'analyse échoue à cause d'un fichier de cache (donnée par nature jetable) | try/except → retourner le modèle vide (priors) + log | S |
| A-06 | `core/autocorrect.py:451` + `core/wb_model.py:144-147` | ⚪ | CONFIRMÉ | `Tint` jamais clampé aux bornes Lr (±150) alors que Temperature l'est (2000-12000) — écarts extrêmes improbables mais non bornés | Clamp ±150 aux deux endroits | S |
| A-07 | `core/seed_match.py:135-144` | ⚪ | CONFIRMÉ | `k = min(3, max(1, pool//2))` : à 3 seeds k=1, à 4-5 k=2 — le « k-NN jusqu'à 3 » annoncé n'est atteint qu'à ≥ 6 seeds ; comportement discutable mais pas faux | Commenter l'intention ou `max(1, min(3, pool))` | S |

---

## Passe 2 — Performance (zones chaudes uniquement)

> Cibles : pipeline image/GPU, cache SQLite, polling pont 300 ms. Pas de micro-optim ailleurs.
> Chaque hotspot doit dire s'il touche les mesures → si oui, impact `ANALYSIS_VERSION` à noter.
> **Aucun profilage exécuté** (pas de GPU sollicitable en revue) : coûts = estimations
> raisonnées depuis le code, à confirmer par `py-spy`/`torch.profiler` avant gros chantier.

Conformité vérifiée (pas de hotspot trouvé) : lookups cache tous sur PRIMARY KEY `uuid`
(aucun index manquant sur le chemin live ; seul prédicat non indexé = `NeutralPreviewJPEG.hash_style`
dans `get_bias_pool`, code sans appelant — cf. DB-06) ; blobs JSON ~1-2 Ko/ligne (sérialisation
négligeable) ; hit-rate structurel sain : clés de fraîcheur alignées get/put par table, un Apply
2ᵉ passage ne redécode que l'aperçu (`force_fresh_preview`) — conforme à l'intention §5.

| ID | file:line | Coût actuel | Cause | Optimisation | Gain estimé | Touche mesures ? |
|---|---|---|---|---|---|---|
| P-01 🟠 | `gpu_schedule.py:71-79` | Phase RAW (coût dominant du 1ᵉʳ passage) : mur ≈ T_unpack_CPU + T_GPU au lieu de max(T_unpack, T_GPU). Le docstring (`:9-12`) promet « le CPU déballe la vague suivante pendant que le GPU traite » — le code ne le fait **pas** | `bayers = list(ex.map(...))` **bloque** jusqu'à l'unpack complet de la vague, puis le GPU traite pendant que le pool CPU est idle ; aucun prefetch de la vague N+1 | Double-buffering : soumettre les futures d'unpack de la vague N+1 avant de traiter la vague N sur GPU (le `ThreadPoolExecutor` existe déjà) | Recouvrement de min(T_unpack, T_GPU) ≈ **20-40 % du mur de la phase RAW** (des minutes sur 500 photos) ; effort M | non |
| P-02 🟠 | `gpu_schedule.py:73` + `:96`, `embedded_jpeg.py:148-168` | Chaque photo manquante ouvre le `.ARW` **deux fois** via `rawpy.imread` : étape 1 (`unpack_raw` → bayer) puis étape 2 (`extract_reference` → WB + octets JPEG). 2 parses conteneur + 2 lectures fichier (~25-50 Mo, la 2ᵉ servie du cache OS mais parse LibRaw payé) | Pipelines RAW et embedded conçus séparément ; les listes de manques sont pourtant alignées (même clé `raw_signature`) | Unpack unifié : une ouverture rawpy retourne `RawBayer` + WB as-shot + octets thumb (les 3 sont déjà lus dans le même `with`), nourrit les deux étapes | ~0,1-0,3 s/photo → **~1-2,5 min sur 500 photos** ; effort M (naturellement combiné à P-01) | non |
| P-03 🟠 | `gpu_schedule.py:34,43-49` + `:79,121,148` | Vagues JPEG dimensionnées avec l'estimation **RAW** (`_EST_BYTES_PER_IMG` ≈ 1,19 Go/img) → ~3-5 images/vague pour des JPEG de 0,5-3 MP (~40-80 Mo réels) ; et `gpu.empty_cache()` **à chaque vague** = sync + flush de l'allocateur torch → cudaMalloc repayé à la vague suivante | Une seule constante de dimensionnement pour deux charges qui diffèrent de ~15× ; `empty_cache` systématique au lieu de réactif | Estimation par pipeline (JPEG ≈ 60-80 Mo/img → vagues de 30-60, nvJPEG enfin amorti — c'est le but affiché de `decode_blobs`) ; `empty_cache` seulement sur OOM ou toutes les N vagues | ~125-170 cycles sync+realloc évités sur 500 photos + vrai batching nvJPEG : **×2-4 sur la phase décode JPEG** (estimation) ; effort S | non |
| P-04 🟡 | `render_metrics_gpu.py:165-177,224-245` | `analyze_rendered_gpu_dual` appelle `band_stats`/`neutral_stats` 2× (global+sharp) : hue/sat recalculés 2×/image, chroma 4× ; surtout `diff = hue.unsqueeze(-1) - _BAND_CENTERS` alloue **H×W×8 float32** (≈ 770 Mo transitoires sur entrée 24 MP côté `gpu_raw:214`, + `circ` idem) — c'est CE pic qui force `_EST_BYTES_PER_IMG` à 36 o/px et donc les petites vagues | Aucun partage des intermédiaires entre les deux portées ; affectation de bande par broadcast 8 centres au lieu d'un binning | Hoister hue/sat/chroma/`band_idx` calculés **une fois** dans la composition dual et les passer aux deux portées ; garder `argmin` (bit-exact). Option : `bucketize` sur bords de bandes (−8× RAM) à valider bit-exact avant adoption | Phase métriques **−30-40 %**, pic VRAM bandes **−50 %** (→ vagues plus grandes, se cumule avec P-03) ; effort M | non (valeurs identiques si argmin conservé ; si bascule bucketize : vérifier parité, sinon bump `ANALYSIS_VERSION`) |
| P-05 🟡 | `render_metrics_gpu.py:62-72,115-199`, `gpu_raw.py:185-206,218-219` | ~50-100 synchronisations GPU→CPU **par image** : chaque `float(...)`/`_q(...)` force un sync (band_stats seul : 8 bandes × ~6 scalaires, ×2 en dual) ; en prime `pp[mask_flat]` (gather N×3) exécuté 2× (`gpu_raw:218-219`) | Réductions rapatriées scalaire par scalaire au fil du code au lieu d'être groupées | Regrouper : quantiles multi-q par appel (`torch.quantile` accepte un tenseur de q), empiler les scalaires d'une image et faire **un** `.cpu()` ; mettre en cache le gather `pp[mask_flat]` | ~10-40 ms/image d'overhead sync → **5-20 s sur 500 photos** ; effort M (gain modeste, à faire en passant sur P-04) | non (mêmes valeurs) |
| P-06 🟡 | `gpu_raw.py:153` | `torch.from_numpy(rb.bayer.astype(np.float32)).to(dev)` : conversion float32 **côté CPU** (alloc+copie 96 Mo pour 24 MP) puis transfert H2D de 96 Mo au lieu de 48 | Conversion faite avant le transfert au lieu d'après | Transférer le uint16 (48 Mo) puis `.float()` sur GPU ; option `pin_memory` + `non_blocking=True` avec les streams déjà exposés (`gpu.streams`, jamais utilisés) | ~5-10 ms/photo → quelques secondes sur 500 ; effort S | non |
| P-07 🟡 | `cache.py:348,361,504,576,608,684` + `autocorrect_worker.py:354-368,405-414,464-468` | `conn.commit()` dans **chaque** `put_*` ; les boucles de collecte committent par photo (étape RAW : 2 commits/photo → ~1 500 commits sur 500 manques). WAL+NORMAL amortit (pas de fsync par commit) mais reste ~0,1-1 ms + verrou/WAL-append chacun | API cache à autocommit, appelée en boucle | Une transaction par étape de collecte (`BEGIN` … commit final, ou `with conn:` autour de la boucle) ; même remède que DB-03 (executemany pour les seeds) | **~0,2-1,5 s par run de 500 photos** + moins de churn WAL ; effort S | non |
| P-08 ⚪ | `autocorrect_worker.py:224,255,324-327,384-387` + `cache.py:364-368` | 3-4 SELECT ponctuels/photo/run (source_raw, in_camera, preview, `is_seed` par photo) + `raw_signature` staté 2× par photo (étapes 1 et 2) | Lookups unitaires en boucle | Non chaud : PK lookups ~20-80 µs → **~50-150 ms totaux sur 500 photos**. Seul geste utile : réutiliser `list_seed_uuids` en set au lieu de `is_seed` par photo | négligeable ; effort S — à faire seulement en passant | non |
| P-09 ⚪ | `PollingLoop.lua:28,151-190`, `api.py:52-60`, `job_queue.py:112-139` | Poll 300 ms = 3,3 req/s ; par requête : parse uvicorn+routage FastAPI+2 locks ≈ 0,2-0,5 ms CPU → **≈ 0,1-0,2 % d'un cœur** côté serveur, un GET LrHttp + sleep côté plugin. Latence de prise en charge : 150 ms en moyenne/job — invisible face aux 4 s/photo des probes | Polling assumé par l'architecture (plugin = toujours client) | **Aucune** : pas un hotspot, ne pas optimiser. (Si un jour la latence compte : long-poll côté serveur — req ÷30, latence ~0 — mais le heartbeat 5 s est couplé à la cadence de poll, à repenser avec L-05) | – | non |
| P-10 ⚪ | `neutral_preview_worker.py:94-97` | `_probe_chunk` décode les miniatures **une par une** (`decode_file` = `decode_blobs([1])`) puis analyse photo par photo — 16 appels nvJPEG par chunk au lieu d'un lot | Boucle écrite au fil du résultat plugin | Batcher via `gpu_schedule.analyze_render_blobs` (existe déjà). Gain réel négligeable : le chemin est dominé par le rendu Lr (budget 4 s/photo) | < 1 % du mur du probe ; effort S — cohérence plus que perf | non |

---

## Passe 3 — Backlog consolidé (priorisé)

> Fusion des passes 0-2. Tri : sévérité décroissante (🔴 > 🟠 > 🟡 > ⚪), puis effort croissant
> (S avant M avant L) ; à sévérité+effort égaux, CONFIRMÉ avant PLAUSIBLE. Aucun 🔴.
> Colonne **Groupe** = lots à traiter ensemble (voir liste des groupes sous la table).

| Rang | ID source | Titre | Sév | Effort | Sous-système | Groupe |
|---|---|---|---|---|---|---|
| 1 | L-03 | Échec de restore du probe silencieux (photo laissée en état neutre) | 🟠 | S | Plugin Lua | G3 |
| 2 | B-01 | `_pending_ids` jamais comparé — Apply rejoue un plan périmé si la sélection change | 🟠 | S | GUI/pont | – |
| 3 | A-01 | exiftool en argv > limite Windows dès ~300 photos → lot entier sans profil | 🟠 | S | Analyse | G2 |
| 4 | P-03 | Vagues JPEG dimensionnées taille-RAW + `empty_cache` chaque vague | 🟠 | S | Pipeline GPU | G7 |
| 5 | L-01 | Retour de `requestJpegThumbnail` jeté → callbacks jamais tirés (GC) | 🟠 | S | Plugin Lua | G3 |
| 6 | L-02 | Fichier de sortie fixe + callbacks tardifs → pixels périmés mesurés | 🟠 | S | Plugin Lua | G3 |
| 7 | C-01 | Garde `cam_mul[G2]==0` manquante (no-op Sony, casse autres boîtiers) | 🟠 | S | Pipeline GPU | G1 |
| 8 | DB-01 | `hash_style` aveugle au Color Grading/Texture/ToneCurve → ancres neutres périmées servies | 🟠 | M | Cache | G1 |
| 9 | P-01 | Aucun recouvrement CPU/GPU dans `process_raw_batch` (mur = somme, pas max) | 🟠 | M | Pipeline GPU | G7 |
| 10 | P-02 | Double ouverture rawpy par photo (unpack + extract embedded) | 🟠 | M | Pipeline GPU | G7 |
| 11 | B-03 | `_anchor_suspect` avale toute exception → ancre suspecte cachée quand même | 🟡 | S | GUI/pont | – |
| 12 | B-04 | `conn` SQLite non fermée sur les chemins d'exception du worker | 🟡 | S | GUI/pont | – |
| 13 | L-04 | Apply partiel : erreurs perdues quand `status='ok'` | 🟡 | S | Plugin Lua | G4 |
| 14 | L-05 | Heartbeat écrit avant dispatch → pont « inactif » pendant les jobs longs | 🟡 | S | Plugin Lua | G5 |
| 15 | L-06 | Body 200 non-JSON traité comme « pas de job », sans log (job perdu 900 s) | 🟡 | S | Plugin Lua | G4 |
| 16 | L-07 | POST du résultat jamais réessayé — travail fait, résultat perdu | 🟡 | S | Plugin Lua | G4 |
| 17 | B-02 | Sérialisation après pop dans `/jobs/pending` : payload non sérialisable = job perdu | 🟡 | S | Serveur | – |
| 18 | C-02 | Échec CUDA transitoire mémoïsé à vie du process (`lru_cache` sur `_diagnose`) | 🟡 | S | Pipeline GPU | – |
| 19 | C-03 | `cv2.imdecode(...)` sans test None dans `raw.py` (crash appels directs tools) | 🟡 | S | Pipeline GPU | – |
| 20 | DB-03 | `_apply_seed_flag` : 2 commits/photo sur le thread Qt → gel GUI | 🟡 | S | Cache | G6 |
| 21 | P-07 | Commit par `put_*` → ~1 500 commits par run de 500 photos | 🟡 | S | Cache | G6 |
| 22 | DB-02 | `hash_jpeg`/`hash_preview` = signature fichier, pas sha1 — doc fausse, `blob_hash` mort | 🟡 | S | Cache | G8 |
| 23 | A-02 | `text=True` sans encoding → chemins accentués mojibake, profils None | 🟡 | S | Analyse | G2 |
| 24 | A-03 | Poses lentes FR (« 0,4 s ») non parsées → `ev100=None` | 🟡 | S | Analyse | – |
| 25 | A-04 | `refine_temp_tint` sans garde `neutral=None` → tout le run échoue | 🟡 | S | Analyse | – |
| 26 | A-05 | JSON cache disque corrompu → `response.load()` fait échouer l'analyse | 🟡 | S | Analyse | – |
| 27 | P-06 | Conversion float32 côté CPU avant H2D (2× le trafic PCIe) | 🟡 | S | Pipeline GPU | G7 |
| 28 | B-05 | Apply = une transaction pour toute la sélection, timeout fixe 180 s | 🟡 | M | GUI/pont | G5 |
| 29 | P-04 | Dual : hue/sat/chroma recalculés par portée + broadcast bandes ≈ 770 Mo transitoires | 🟡 | M | Pipeline GPU | G9 |
| 30 | P-05 | ~50-100 syncs GPU→CPU par image (scalaires rapatriés un à un) | 🟡 | M | Pipeline GPU | G9 |
| 31 | L-09 | Apply sans repli `findPhotoByUuid` (photos sautées si sélection change) | ⚪ | S | Plugin Lua | – |
| 32 | L-08 | Décodage `\u` sans paires surrogates (chemin quasi jamais pris) | ⚪ | S | Plugin Lua | – |
| 33 | B-06 | 204 avec body `{}` (RFC ; casse sur repli h11) | ⚪ | S | Serveur | – |
| 34 | DB-04 | Repli `"0:0"` non salé `ANALYSIS_VERSION` (collision théorique) | ⚪ | S | Cache | – |
| 35 | DB-05 | `ORDER BY cached_at DESC LIMIT 1` sur PK (code mort trompeur) | ⚪ | S | Cache | G8 |
| 36 | A-06 | `Tint` jamais clampé ±150 | ⚪ | S | Analyse | – |
| 37 | A-07 | k-NN : k=3 annoncé atteint seulement à ≥ 6 seeds (documenter l'intention) | ⚪ | S | Analyse | – |
| 38 | P-08 | N+1 lookups PK + `is_seed` par photo (~100 ms/500 photos — non chaud) | ⚪ | S | Cache | – |
| 39 | P-10 | `_probe_chunk` décode les miniatures une à une (dominé par le rendu Lr) | ⚪ | S | GUI/pont | – |
| 40 | D-01…D-05 | Lot doc : 44 clés (pas 42), types `models.py` omis, fourchette dispatch, docstrings `analysis`/`seed_match` périmées | ⚪ | S | Doc | G8 |
| 41 | DB-06 | `get_bias_pool` sans appelant (mort ou à câbler — décision archi) | ⚪ | – | Cache | G8 |
| 42 | C-04 | Quantiles GPU sous-échantillonnés > 8 M px — préciser ARCHITECTURE §4 | ⚪ | – | Doc | G8 |
| 43 | C-05 | `extract_jpeg_stream` premier SOI — commentaire seulement | ⚪ | – | Doc | G8 |
| 44 | P-09 | Poll 300 ms : ≈ 0,1-0,2 % d'un cœur — **ne pas optimiser** | ⚪ | – | Pont | – |

### Groupes (doublons / findings liés)

- **G1 — Bump `ANALYSIS_VERSION` commun** : DB-01 + C-01. Les deux exigent un bump (rebuild
  complet du cache) → les livrer **dans le même commit** pour ne payer qu'un seul rebuild.
- **G2 — exiftool** : A-01 + A-02. Même fonction (`exif_profile.py:83-88`), un seul patch
  (argfile `-@` + `encoding="utf-8"` + charset).
- **G3 — Thumbnails probe** : L-01 + L-02 + L-03. Même flux (`Thumbnails.lua` fetch/fetchProbe),
  une seule passe de durcissement (rétention des retours, nonce fichier, erreurs de restore remontées).
- **G4 — Robustesse résultat PollingLoop** : L-04 + L-06 + L-07. Même fichier, même thème
  (résultat de job perdu/append silencieux).
- **G5 — Jobs longs vs heartbeat** : L-05 + B-05 (et la note long-poll de P-09). Le chunking de
  l'apply (B-05) réduit mécaniquement la fenêtre où le heartbeat gèle (L-05) — concevoir ensemble.
- **G6 — Écritures SQLite groupées** : DB-03 + P-07. Même remède : transactions par lot
  (executemany / commit par étape), et sortir les écritures du thread Qt.
- **G7 — Refonte scheduler GPU** : P-01 + P-02 + P-03 (+ P-06 en passant). Un seul chantier
  `gpu_schedule` : unpack unifié (1 ouverture rawpy), prefetch double-buffer, dimensionnement
  par pipeline, `empty_cache` réactif. Valider par `tools/validate_gpu_vs_libraw` (parité inchangée).
- **G8 — Doc & code mort** : D-01…D-05 + DB-02 + DB-05 + DB-06 + C-04 + C-05. Lot documentaire /
  suppression de code mort — alimente PLAN étapes 1 et 7.
- **G9 — Micro-passe métriques GPU** : P-04 + P-05. Mêmes fichiers (`render_metrics_gpu`,
  `gpu_raw`), à faire ensemble, parité bit-exacte exigée (sinon bump `ANALYSIS_VERSION`).

### Proposition de mise à jour PLAN.md / ARCHITECTURE.md (✅ APPLIQUÉE 2026-07-18, Passe 4)

**PLAN.md** — 3 modifications proposées :

1. Étape 1 (suppression `analysis_worker`), ajouter la nuance de la Passe 0 :

```diff
 - [ ] **1 — Supprimer le code mort.**
   Supprimer `app/gui/analysis_worker.py` (`AnalysisWorker` jamais instancié ni importé — vérifié).
+  Note (revue Fable 5, Passe 0) : `analysis_worker` est le seul importeur GUI direct de
+  `gpu_raw`/`raw` — leur statut live tient par la chaîne `gpu_schedule`/`embedded_jpeg`.
+  Sa suppression ne tue aucun module core, mais fait de `gpu_schedule` l'unique entrant de `gpu_raw`.
```

2. Nouvelle section backlog après « Backlog restant » (items 🟠 de la revue, groupés) :

```diff
+## Backlog revue Fable 5 (2026-07-18) — items 🟠 (détail : documentation/REVIEW_FABLE5.md, Passe 3)
+
+- [ ] **G3 — Durcir `Thumbnails.lua`** (L-01/L-02/L-03) : retenir les retours de
+  `requestJpegThumbnail`, nom de fichier unique par appel, remonter les échecs de restore.
+- [ ] **B-01 — Vérifier `_pending_ids` avant Apply** : re-fetch sélection, re-planifier si écart.
+- [ ] **G2 — exiftool argfile** (A-01/A-02) : `-@ argfile` + `encoding="utf-8"` (limite argv
+  Windows dès ~300 photos ; chemins accentués).
+- [ ] **G1 — Bump `ANALYSIS_VERSION` commun** (DB-01/C-01) : compléter `DEVELOP_KEYS` (Lua) et
+  `_STYLE_KEYS` (Color Grading/Texture/ToneCurve/Parametric), garde `cam_mul[G2]==0` dans
+  `gpu_raw` — un seul bump, un seul rebuild.
+- [ ] **G7 — Refonte scheduler GPU** (P-01/P-02/P-03, +P-06) : unpack unifié (1 ouverture rawpy),
+  prefetch double-buffer CPU/GPU, dimensionnement de vague par pipeline, `empty_cache` réactif.
+  Parité à revalider (`tools/validate_gpu_vs_libraw`).
```

3. Ligne perf du backlog existant, à corriger (la Passe 2 contredit « déjà couverte ») :

```diff
-- Perf : parallélisation des séries 500-1000 déjà couverte par GPU + cache ; re-profiler avant tout Rust.
+- Perf : GPU + cache en place, mais la Passe 2 (REVIEW_FABLE5.md) identifie des pertes
+  structurelles (pas de recouvrement CPU/GPU, double ouverture rawpy, vagues JPEG sous-dimensionnées).
+  Profiler (`py-spy`/`torch.profiler`) puis traiter G7 avant d'envisager Rust.
```

**ARCHITECTURE.md** — corrections Passe 0 (statuts §3 : **zéro changement**, la carte est exacte) :

```diff
 §2, table plugin (ligne ~80) :
-| `PhotoData.lua` | Extraction path/EXIF/develop settings/catalog_path (**42 `DEVELOP_KEYS`**) |
+| `PhotoData.lua` | Extraction path/EXIF/develop settings/catalog_path (**44 `DEVELOP_KEYS`**) |

 §2, types de jobs (lignes ~66-67) :
-(≈ lignes 43-149)
+(≈ lignes 47-121)

 §3, `server/` (lignes ~143-144) :
-`models.py` (Pydantic : `Job`, `JobResult`, `PhotoResult`, `ExifData`, `PhotoAdjustment`, enum `JobType`).
+`models.py` (Pydantic : `Job`, `JobResult`, `PhotoResult`, `ThumbnailResult`, `ExifData`,
+`PhotoAdjustment`, enums `JobType` / `JobStatus`).
```

D-04/D-05 (docstrings périmées dans `core/analysis.py:12` et `core/seed_match.py:1`) = patchs
code, pas doc projet → rangés dans G8.

---

## Journal des passes

| Passe | Date | Effort raisonnement | Sous-agents utilisés | Findings |
|---|---|---|---|---|
| 0 | 2026-07-17 | standard + recoupement manuel des chaînes d'imports | 4 × cavecrew-investigator en parallèle (imports core / imports gui+server / require Lua / fact-check serveur+cache) ; 3 erreurs d'agent corrigées par grep direct (gpu_raw, catalog, wb_model comptés à tort tools-only ou via module mort) | Carte §3 : 0 correction de statut (49 modules audités). 5 divergences doc↔code (D-01 à D-05), toutes mineures ; 10 familles de claims vérifiées conformes |
| 1 | 2026-07-17 | élevé — lecture intégrale des 14 Lua (~1 200 l.) + serveur/GUI/core live (~4 700 l. Python), APIs Lua confrontées à `lr15_sdk_api_reference.md`, parité CPU↔GPU vérifiée formule par formule | aucun (lecture directe, pas de sous-agent) | 33 findings : 7 🟠 · 16 🟡 · 10 ⚪ · 0 🔴. Têtes de liste : DB-01 (hash_style aveugle au Color Grading/Texture → ancres neutres périmées), L-03 (échec de restore du probe silencieux), B-01 (`_pending_ids` jamais vérifié), A-01 (exiftool argv > limite Windows sur 500+ photos), L-01/L-02 (requestJpegThumbnail : rétention + course de fichier), C-01 (garde G2=0 manquante — no-op Sony) | 
| 2 | 2026-07-18 | standard — lecture ciblée des 3 zones chaudes (gpu_schedule/gpu_raw/gpu_jpeg/render_metrics_gpu/sharpness, cache.py + boucles workers, PollingLoop/api/job_queue) ; **aucun profilage exécuté**, coûts = estimations raisonnées marquées comme telles | aucun (lecture directe) | 10 hotspots : 3 🟠 (P-01 pas de recouvrement CPU/GPU — le docstring promet l'inverse ; P-02 double ouverture rawpy/photo ; P-03 vagues JPEG taille-RAW + empty_cache systématique) · 4 🟡 (P-04 broadcast bandes ~770 Mo transitoires + recalculs dual ; P-05 syncs scalaires ; P-06 float32 CPU avant H2D ; P-07 ~1 500 commits/run) · 3 ⚪ dont P-09 : poll 300 ms **non chaud** (≈ 0,1-0,2 % cœur), à ne pas optimiser. Cache : aucun index manquant sur le chemin live. Aucun hotspot ne touche les mesures (bump `ANALYSIS_VERSION` requis seulement si P-04 bascule sur bucketize non bit-exact) |
| 3 | 2026-07-18 | standard — consolidation pure (aucune nouvelle analyse de code), fusion des 49 findings des passes 0-2 | aucun | Backlog unique de 44 lignes (10 🟠 · 20 🟡 · 14 ⚪), tri sévérité puis effort ; 9 groupes de findings liés (G1 bump ANALYSIS_VERSION commun DB-01+C-01, G7 refonte scheduler GPU P-01/02/03…) ; diffs PLAN.md (3) et ARCHITECTURE.md (3) **proposés, non appliqués** — en attente de validation utilisateur |
| 4 (implémentation) | 2026-07-18 | élevé — exécution de la refonte sur validation utilisateur | aucun (édition directe) | **Livré : 40/44 items** — tous les 🟠 (G1 bump `v5-style-keys-g2wb` + _STYLE_KEYS corrigées (5 noms SDK inexistants `ColorGradeShadowHue`… → `SplitToning*`) + `DEVELOP_KEYS` 44→71 ; G2 argfile ; G3 rétention/nonce/restore_error ; G4 errors_summary/log 200/retry POST ; G5 heartbeat en attente + apply chunké 50 + timeout ∝ n ; B-01 re-check sélection ; G7 `process_combined_batch` double-buffer/unpack unifié/vagues par pipeline/H2D uint16) ; 🟡/⚪ : B-02/03/04/06, C-02/03, DB-02/03/04/05, A-03/04/05/06/07, L-08/09, P-07, D-01…D-05, C-04/05, DB-06 (biais mort SUPPRIMÉ : `get_bias_pool`, `_build_bias_by_group`, `compute_profile_bias`, `blob_hash`) ; PLAN étape 1 (analysis_worker supprimé + `test_no_dead_modules.py`). **Non traité** : G9 (P-04/P-05, parité bit-exacte à profiler), P-10, P-08 (non chauds). Validation : 78/78 pytest, `validate_gpu_vs_libraw` 3 ARW (expo corr 1.000, gray-world 0.996-0.9995), smoke `process_combined_batch` 4/4 réels |
