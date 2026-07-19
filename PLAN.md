# PLAN — Fiabiliser HSL & Calibration

Roadmap exécutable **une étape à la fois**. Contexte technique : [`documentation/ARCHITECTURE.md`](documentation/ARCHITECTURE.md).
Règles de travail : [`CLAUDE.md`](CLAUDE.md). Historique nettoyage (étapes 1-7, backlogs perf/Fable5) :
[`OLD_PLAN.md`](OLD_PLAN.md).

## Origine

Audit 2026-07-19 des axes HSL (`core/hsl.py`) et Calibration caméra (`core/seed_match.py` +
`autocorrect._calib_develop_dict`) : 2 failles sur HSL (garde RAW jamais câblée, gain de
curseur jamais calibré — toujours nominal), 3 lacunes sur Calibration (non validée en Lr réel,
pas de garde de cohérence k-NN, distance de matching scène-dépendante non tranchée). Détail
complet dans la conversation du 2026-07-19 (à reporter dans `documentation/ARCHITECTURE.md`
§ Limitations une fois corrigé).

## Règles d'exécution (Sonnet 5)

1. Faire **une seule étape** à la fois, dans l'ordre.
2. Pour chaque étape : **implémenter le test de non-régression AVANT/AVEC le changement**.
3. Valider par `python -m pytest app/tests -q` (doit rester **vert**, y compris les tests existants).
4. **Cocher `- [ ]` → `- [x]` uniquement après test vert.** Ne jamais cocher une étape non validée.
5. Ne pas ajouter de repli CPU (GPU-strict). Ne pas toucher au comportement live sans test qui le couvre.
6. Si une étape casse un test existant sans raison légitime : arrêter, ne pas cocher, signaler.
7. Étapes marquées **⚠️ Lr requis** : non automatisables ici (pas de Lr dans cet environnement) —
   implémenter + tester ce qui est testable sans Lr, documenter la partie manuelle en annexe.

---

## Étapes — HSL

- [x] **H1 — Câbler ou retirer la garde `raw_oversat`.** ✅ Câblée (2026-07-19) : nouveau
  `hsl.raw_confirms_oversat(raw_band)` (seuil `_SAT_CLIP_TRIGGER` réutilisé, pas de seuil
  inventé) ; `PhotoMeasure.raw_bands` ajouté (RAW zone nette, cache `SourceRAW.hsl_sharp`
  déjà présent mais jamais transmis) ; câblé dans `_embedded_band_targets` et
  `_band_targets_from_seed_match` (RAW de la photo **cible**, pas des seeds) ; worker
  (`autocorrect_worker.py`) alimente `raw_bands=raw_d.get("bands")`. Tests :
  `test_hsl.py` (8 cas plan_band + raw_confirms_oversat) et 5 cas de wiring dans
  `test_autocorrect_helpers.py`. `python -m pytest app/tests -q` : 103 passed.
  `hsl.BandTarget.raw_oversat` est documentée comme garde anti-sur-correction (« ne réduire la
  saturation d'une bande que si le RAW confirme qu'elle est chargée à la capture ») mais aucun
  appelant (`autocorrect._embedded_band_targets`, `_band_targets_from_seed_match`) ne la
  renseigne — `raw_blocks` toujours `False`, garde morte.
  Décision à prendre en 1er : **câbler** (mesurer chroma de la bande sur le RAW zone nette,
  `sharpness`/`analysis`, comparer à un seuil) ou **retirer** (champ + doc + docstring) si jugée
  hors périmètre. Ne pas laisser un garde-fou documenté qui ne protège rien.
  - *Test non-rég* : si câblée, `app/tests/test_hsl.py` cas `raw_oversat=False` bloque bien une
    réduction de saturation même avec excès de chroma mesuré. Si retirée : `grep -rn
    "raw_oversat" app` ne renvoie plus rien hors historique git.
  - *Valider* : `python -m pytest app/tests -q` vert.

- [x] **H2 — Sondage `render_probe` pour calibrer `ResponseModel.bands`.** ✅ (2026-07-19) :
  `core.response.fit_linear_response(deltas, measured)` — régression linéaire pure (ordonnée
  libre, moindres carrés) ; retombe sur `0.0` (non calibré) si <2 échantillons ou deltas non
  dispersés. Outil `tools/calibrate_hsl_response.py` : démarre le serveur App (headless, sans
  GUI — même processus que `app.main`, requis car `job_queue` est un singleton in-process),
  attend le pont, prend la photo sélectionnée dans Lr, sonde chaque bande × axe
  (Saturation/Luminance/Hue) delta par delta via des jobs `render_probe` séquentiels (pas de
  lot — évite de supposer un ordre de résultat non confirmé), mesure GPU
  (`render_metrics_gpu.analyze_rendered_gpu`), fit, `response.save()`. Teinte réunwrappée
  (circulaire) avant fit. Tests : `test_response.py` — 5 cas `fit_linear_response` (pente
  positive, négative, <2 échantillons, deltas non dispersés, longueurs différentes) sur
  données synthétiques. `python -m pytest app/tests -q` : 108 passed.
  - *Test non-rég* : fonction de fit pure testée sur données synthétiques (delta curseur connu
    → pente attendue). Pas de test Lr ici (⚠️ Lr requis pour le sondage réel — non exécuté,
    aucun environnement Lr disponible ici ; script prêt, à lancer manuellement).
  - *Valider* : `python -m pytest app/tests -q` vert.

- [ ] **H3 — Garde sur le transplant embedded (luminance/teinte).**
  Mode embedded (`ignore_bias=True`) transplante la bande JPEG boîtier brute sur chroma **et**
  luminance/teinte, sans le garde-fou « réduction seule » qui protège la saturation. Le JPEG
  boîtier a sa propre science couleur (profil créatif) — risque de transplanter un biais L*/hue
  qui n'est pas le but recherché. Ajouter une zone morte plus stricte ou un plafond dédié pour
  L*/hue en mode `ignore_bias=True`, cohérent avec l'esprit « corriger, pas copier ».
  - *Test non-rég* : `test_hsl.py` / `test_autocorrect_helpers.py` — cas cible JPEG à L*/hue
    fortement décalé, vérifier plafond appliqué (pas de transplant intégral).
  - *Valider* : `python -m pytest app/tests -q` vert.

## Étapes — Calibration caméra

- [ ] **C1 — Validation manuelle en Lightroom réel.** ⚠️ Lr requis.
  Backlog déjà noté (`OLD_PLAN.md`) : jamais vérifié en conditions réelles. Sur un catalogue de
  test, éditer le panneau Calibration caméra (ShadowTint, Red/Green/Blue Hue+Sat) sur 2-3 seeds,
  « Marquer + analyser références », puis Aperçu/Appliquer axe `calib` sur une photo cible.
  Vérifier : `EnableCalibration` posé, les 7 valeurs transplantées correspondent au k-NN attendu,
  pas de régression sur les autres axes (expo/wb/hsl) appliqués en même temps.
  - *Test* : pas de test automatisé possible (dépend Lr) — documenter le résultat dans ce plan
    (case cochée seulement après vérif manuelle réussie, avec note de ce qui a été observé).

- [ ] **C2 — Garde de cohérence k-NN avant transplant.**
  `_calib_develop_dict` transplante la moyenne pondérée des k seeds sans vérifier qu'ils
  s'accordent. Si les k seeds matchés divergent fortement sur un champ (ex. `RedHue` : +30 vs
  −20), la moyenne pondérée produit une valeur qui ne correspond à aucun seed réel. Ajouter un
  seuil de dispersion (écart-type ou spread max) par champ ; au-delà, ne pas transplanter ce
  champ (ou ne garder que le seed le plus proche) plutôt que moyenner à l'aveugle.
  - *Test non-rég* : `test_seed_match.py` — cas 2 seeds proches en distance mais divergents sur
    `shadow_tint` → champ omis (ou repli 1-seed) au lieu de moyenne aveugle. Cas seeds cohérents
    → transplant inchangé (pas de régression).
  - *Valider* : `python -m pytest app/tests -q` vert.

- [ ] **C3 — Trancher distance scène vs constante caméra.**
  Question ouverte : le vecteur de distance k-NN (asshot_rg, asshot_bg, raw_median_l — conditions
  de **scène**) est réutilisé tel quel pour Calibration, alors que ces réglages corrigent
  plutôt le capteur/corps caméra (souvent quasi constants, sauf éclairage mixte). Investiguer
  sur seeds réels retouchés (une fois C1 fait, jeu de données disponible) : la valeur Calibration
  varie-t-elle avec la scène chez cet utilisateur, ou est-elle stable par caméra ? Si stable :
  remplacer le k-NN par une médiane/mode globale par caméra (plus robuste, moins de bruit) pour
  cet axe uniquement, garder k-NN scène pour expo/wb/hsl.
  - *Test non-rég* : selon décision — si repli médiane globale, `test_seed_match.py` couvre le
    nouveau chemin (`calib` indépendant de la distance scène) sans casser expo/wb/hsl.
  - *Valider* : `python -m pytest app/tests -q` vert. Décision documentée ici avant de coder
    (ne pas changer sans données seeds réelles à l'appui — cf. CLAUDE.md, ne pas inventer).

---

## Backlog nettoyage (reporté)

Étapes 2-7 de l'ancien plan (config pytest, garde anti-désync docs, couverture tests modules
purs, hygiène logging, cleanup QThread, resync docs) + backlogs perf (G9, P-10) + repli régime
artistique + `core/image_source.py` tool-only : **non abandonnés, reportés après H1-H3/C1-C3**.
Détail intact dans [`OLD_PLAN.md`](OLD_PLAN.md).

## Annexe — Validation dépendante de Lightroom (manuelle si pas de MCP)

- H2 : sondage `render_probe` réel pour calibrer `ResponseModel.bands` (résultat à noter ici une
  fois fait : caméra/profil, pentes mesurées).
- C1 : résultat de la validation manuelle Calibration (cf. étape C1).
- Reportés d'`OLD_PLAN.md` : verrou `requestJpegThumbnail` (convergence 2ᵉ Aperçu ≈ 0), test e2e
  seeds→Aperçu→Appliquer, stubs GUI `photo_panel`/`analysis_panel`.
