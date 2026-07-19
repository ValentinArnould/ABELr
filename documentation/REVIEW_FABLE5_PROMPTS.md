# Prompts de lancement — Revue Fable 5

Mode d'emploi :
1. `/model` → `claude-fable-5`.
2. **Une session neuve par passe** (ou `/clear` entre chaque). Ne jamais enchaîner deux passes
   dans le même contexte : la passe précédente pollue les findings.
3. Copier-coller le prompt de la passe. Chaque passe écrit sa section dans
   [`REVIEW_FABLE5.md`](REVIEW_FABLE5.md).
4. Ordre imposé : 0 → 1 → 2 → 3. La passe 0 fige le périmètre vivant/mort dont dépend tout le reste.

Rappels valables pour TOUTES les passes (déjà dans CLAUDE.md, mais faux positifs fréquents) :
- **GPU-strict = choix voulu.** L'absence de fallback CPU n'est pas un bug.
- **Lua 5.1** : `//`, `goto`, `utf8` stdlib absents = normal.
- Toute reco touchant l'algo de mesure impose un bump de `ANALYSIS_VERSION` (pas de migration) — le dire.
- Aucun finding sans `file:line`. Noms de paramètres develop vérifiés contre `lr15_sdk_api_reference.md`.

---

## PASSE 0 — Vérité terrain (archi / doc)

```
Revue projet ABELr, PASSE 0 sur 4 : vérité terrain architecture/doc. Objectif =
figer le périmètre vivant/mort AVANT toute chasse aux bugs. Ne corrige rien, ne propose aucun
fix de code : cette passe est purement descriptive.

Lis d'abord : CLAUDE.md, documentation/ARCHITECTURE.md (surtout §3 carte des modules), PLAN.md.

Tâches :
1. Pour CHAQUE module de app/core/ (24 fichiers), app/server/ (3), app/gui/ (7) et les 14
   fichiers Lua de ABELr.lrplugin/ : déterminer le statut réel (live / tool-only / mort)
   en cherchant les références entrantes (imports, require, appels). Un module sans importeur
   hors app/tests/ et hors app/tools/ est candidat "mort" ou "tool-only".
2. Comparer ce statut réel au statut annoncé dans ARCHITECTURE.md §3. Lister les écarts.
3. Relever les divergences doc↔code hors carte modules : endpoints FastAPI, types de jobs,
   pipeline image, schéma cache (nb de tables), contraintes CLAUDE.md — tout ce que la doc
   affirme et que le code contredit.

Pour localiser sans saturer le contexte, délègue les recherches "qui importe X / qui appelle Y"
à l'agent cavecrew-investigator.

Sortie : remplis UNIQUEMENT les sections "Passe 0" de documentation/REVIEW_FABLE5.md
(tables 0.1 et 0.2). Chaque ligne avec preuve file:line. Rien sans preuve. Puis renseigne la
ligne Passe 0 du Journal.
```

---

## PASSE 1 — Bugs par sous-système

```
Revue projet ABELr, PASSE 1 sur 4 : chasse aux bugs (correctness). Ne traite QUE les
modules marqués "live" en Passe 0 (section Passe 0 de documentation/REVIEW_FABLE5.md). Ignore
le code mort/tool-only côté bugs.

Lis d'abord : CLAUDE.md, documentation/ARCHITECTURE.md, la section Passe 0 de REVIEW_FABLE5.md.
Pour tout code Lua ou tout nom de paramètre develop : documentation/lr15_sdk_api_reference.md.

Balaie sous-système par sous-système, dans cet ordre, un à la fois :
  (a) Plugin Lua — ABELr.lrplugin/ (14 fichiers). Vérifier : écritures catalog/develop
      dans catalog:withWriteAccessDo ; I/O bloquant dans LrTasks.startAsyncTask ; LrHttp.post
      dans postAsyncTaskWithContext ; chemins via LrPathUtils (jamais de concat "/") ;
      import 'LrXxx' vs require ; usage correct de Json.array. Signaler tout PV2012 manquant
      (Exposure2012…) et WhiteBalance='Custom' requis pour Temperature/Tint.
  (b) Pont HTTP + serveur — app/server/api.py, job_queue.py, models.py + HttpClient.lua,
      PollingLoop.lua. Vérifier : cycle de vie du polling (génération vs flag partagé),
      contrat job_id/type/payload, gestion d'erreur réseau, désérialisation, races sur la queue.
  (c) Core image/GPU — raw, gpu, gpu_raw, gpu_jpeg, gpu_schedule, pipeline, image_source, color,
      render_metrics, render_metrics_gpu, embedded_jpeg, previews. Vérifier : gpu.require_cuda
      aux points d'entrée, espaces colorimétriques (ProPhoto linéaire float32, luminance Y,
      use_camera_wb), dtype/normalisation, libération mémoire GPU, chemins RAW Windows.
  (d) Cache SQLite — cache.py + hash dans measure.py, exif_profile.py. Vérifier : les 5 tables,
      ANALYSIS_VERSION salée dans TOUS les hash concernés, cohérence clé/insert/lookup,
      invalidation, transactions.
  (e) Analyse/seed-match — analysis, measure, seed_match, wb_model, exposure, hsl, autocorrect,
      sharpness, regime, response, catalog. Vérifier : correctness numérique, divisions par zéro,
      bornes HSL (saturation = réduction seule), k-NN, régression WB, cohérence unités
      (espace rendu L* pour l'expo).

Rappels anti-faux-positif : GPU-strict sans fallback CPU = voulu ; Lua 5.1 sans //,goto,utf8 =
normal ; reco touchant les mesures ⇒ mentionner le bump ANALYSIS_VERSION.

Sortie : remplis les tables (a)–(e) de la section "Passe 1" de documentation/REVIEW_FABLE5.md.
Colonnes : file:line, Sévérité (🔴/🟠/🟡/⚪), Statut (CONFIRMÉ/PLAUSIBLE), Problème, Fix, Effort.
Rien sans file:line. N'applique aucun patch. Puis renseigne la ligne Passe 1 du Journal.
```

---

## PASSE 2 — Performance

```
Revue projet ABELr, PASSE 2 sur 4 : performance, zones chaudes UNIQUEMENT. Pas de
micro-optimisation généraliste, pas de réécriture cosmétique.

Lis d'abord : CLAUDE.md, documentation/ARCHITECTURE.md (§4 pipeline image, §5 cache), la section
Passe 0 de documentation/REVIEW_FABLE5.md.

Cibles autorisées, dans l'ordre :
  1. Pipeline image / GPU — décodage RAW, transferts CPU↔GPU, batch, réutilisation buffers,
     opérations redondantes par photo (raw, gpu_raw, gpu_jpeg, pipeline, render_metrics_gpu).
  2. Cache SQLite — coût des lookups, index manquants, requêtes N+1, sérialisation des blobs,
     hit-rate effectif (cache.py).
  3. Polling du pont — GET /jobs/pending toutes les 300 ms : coût côté serveur/plugin,
     réveils inutiles, granularité (PollingLoop.lua, api.py, job_queue.py).

Pour chaque hotspot : quantifie le coût actuel (ou décris pourquoi il est chaud), la cause
racine, l'optimisation, le gain estimé, et indique s'il touche l'algo de mesure (si oui ⇒
bump ANALYSIS_VERSION requis). Ne pas casser le contrat GPU-strict ni le versioning cache.

Sortie : remplis la table "Passe 2" de documentation/REVIEW_FABLE5.md. N'applique aucun patch.
Puis renseigne la ligne Passe 2 du Journal.
```

---

## PASSE 3 — Consolidation / backlog

```
Revue projet ABELr, PASSE 3 sur 4 : consolidation. Aucune nouvelle analyse de code —
uniquement synthèse des passes 0 à 2.

Lis documentation/REVIEW_FABLE5.md en entier (sections Passe 0, 1, 2).

Tâches :
1. Fusionner tous les findings des passes 0–2 en un backlog unique. Tri : sévérité décroissante
   (🔴 > 🟠 > 🟡 > ⚪), puis effort croissant (S avant L). Remplir la table "Passe 3".
2. Détecter les doublons / findings liés entre sous-systèmes et les regrouper.
3. Proposer une mise à jour de PLAN.md : intégrer les items 🔴/🟠 dans le backlog du projet,
   corriger ARCHITECTURE.md §3 selon la Passe 0. Présente les diffs proposés, n'écris dans
   PLAN.md / ARCHITECTURE.md qu'après validation explicite de l'utilisateur.

Sortie : table "Passe 3" de REVIEW_FABLE5.md remplie + ligne Passe 3 du Journal. Les
modifications de PLAN.md/ARCHITECTURE.md restent en proposition tant que non validées.
```
