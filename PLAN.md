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

- [x] **H3 — Garde sur le transplant embedded (luminance/teinte).** ✅ (2026-07-19) :
  `BandTarget.embedded_raw: bool = False` — marque une cible transplant brut JPEG boîtier
  (`ignore_bias=True`). `hsl.plan_band` applique un plafond dédié plus strict quand
  `embedded_raw=True` : `_MAX_LUM_EMBEDDED_RAW=10` (vs `_MAX_LUM=20`),
  `_MAX_HUE_EMBEDDED_RAW=8` (vs `_MAX_HUE=15`) — la saturation avait déjà sa garde
  réduction-seule, L*/teinte n'en avaient aucune. Câblé uniquement dans
  `_embedded_band_targets(ignore_bias=True)` ; le mode historique (`ignore_bias=False`,
  delta vs norme de biais) et `_band_targets_from_seed_match` (cibles k-NN déjà validées,
  pas un transplant brut) gardent `embedded_raw=False` par défaut → pas de régression.
  Tests : `test_hsl.py` (3 cas — plafond strict actif, plafond nominal inchangé par défaut,
  luminance et teinte) et `test_autocorrect_helpers.py` (2 cas — wiring
  `ignore_bias=True/False`, non-wiring seed-match). `python -m pytest app/tests -q` :
  113 passed.
  - *Test non-rég* : `test_hsl.py` / `test_autocorrect_helpers.py` — cas cible JPEG à L*/hue
    fortement décalé, vérifier plafond appliqué (pas de transplant intégral).
  - *Valider* : `python -m pytest app/tests -q` vert.

## Étapes — Calibration caméra

- [x] **C1 — Validation manuelle en Lightroom réel.** ✅ (2026-07-19, Lr live + plugin connecté
  pendant la session — catalogue réel, dossier « 2- Dernier soir Abreu », caméra ILCE-7M4).
  Pas de GUI PySide6 lancée : seed_match/`_calib_develop_dict` appelés directement sur RAW réels
  (`gpu_raw.analyze_raw_gpu`) via MCP `abelr`, cf. CLAUDE.md § chemin rapide de validation.
  Protocole : 3 seeds (SML03779, SML03872, SML04799) reçoivent chacun 7 valeurs Calibration
  distinctes et exagérées via `apply_adjustments` (écriture durable, ré-vérifiée par re-lecture
  develop) ; cible SML04057 choisie pour être sans ambiguïté la plus proche d'un seul seed
  (SML04799, distance z-score 1.11 vs 2.44/2.47 pour les 2 autres — k=1 sur pool de 3 comme prévu
  par `seed_match.K_MAX`/`len(pool)//2`). `seed_match.k_nearest` + `target_from_seeds` +
  `autocorrect._calib_develop_dict` reproduits sur ces vecteurs réels (pas de mock) :
  - `EnableCalibration=True` posé.
  - Les 7 valeurs transplantées (ShadowTint=-25, RedHue=-18, RedSaturation=-22, GreenHue=14,
    GreenSaturation=-6, BlueHue=-35, BlueSaturation=19) correspondent **exactement** aux valeurs
    du seed SML04799 (k=1 → pas de moyenne, transplant brut) — aucune contamination par les 2
    autres seeds (distance 2x plus grande).
  - Dict retourné ne contient que les clés Calibration + `EnableCalibration` — pas de recouvrement
    possible avec les clés expo/wb/hsl (`Exposure2012`, `Temperature`/`Tint`,
    `SaturationAdjustment*`…) : pas de régression structurelle sur les autres axes.
  - Round-trip Lr réel via `render_probe` (write temporaire + rendu + restore) sur la cible :
    application acceptée sans erreur, aperçu rendu (fort virage cyan cohérent avec BlueHue=-35/
    GreenHue=+14/ShadowTint=-25 — valeurs volontairement exagérées pour un test non-ambigu),
    `restore_error` absent, develop de la cible confirmé revenu à l'état d'origine après coup.
  Nettoyage : les 3 seeds ré-écrits à leurs valeurs Calibration d'origine (0, `EnableCalibration`
  déjà `true` avant la session) après le test — catalogue réel laissé dans l'état constaté au
  début de la session. `python -m pytest app/tests -q` : 113 passed (aucun code touché, C1 est
  une validation manuelle pure).
  - *Limite* : validation faite en appelant `core/` directement (RAW réels, pas de mock), sans
    passer par la GUI PySide6 (`main_window.py`/`autocorrect_worker.py`) ni par le marquage
    `is_seed` en cache SQLite — ce chemin GUI+cache bout-en-bout reste non exercé par ce test
    (seule la mécanique k-NN + écriture Lr réelle l'est). À couvrir si un doute apparaît côté GUI.

- [x] **C2 — Garde de cohérence k-NN avant transplant.** ✅ (2026-07-19) :
  `seed_match._weighted_calib_field` (remplace `_weighted_field`, seul appelant — les 7 champs
  `CALIB_FIELDS`) — si les valeurs des seeds matchés sur un champ divergent de plus de
  `_CALIB_SPREAD_MAX=25` points curseur (échelle -100..100), refuse la moyenne pondérée et replie
  sur la valeur exacte du seed le plus proche en distance (pas de champ fabriqué qui ne
  correspondrait à aucun seed réel). Seuil provisoire (pas de données seeds réelles conflictuelles
  pour le trancher — cf. C3 non tranché), choisi dans le même ordre de grandeur que
  `hsl._MAX_SAT=25`. `temperature`/`tint`/`tone`/`bands` non touchés (chemin séparé,
  `_weighted_field` n'était utilisé que pour Calibration). Tests : `test_seed_match.py` — 2 cas
  (spread 50 → repli seed proche exact ; spread 2 → moyenne pondérée inchangée) + tests
  existants (`test_target_from_seeds_aggregates_calibration_weighted` : spread 40 sur `red_hue`
  reste `> 19.0` car repli = valeur exacte du seed proche 20.0 — coïncide avec l'ancien
  comportement dominé par le même seed, pas de régression). `python -m pytest app/tests -q` :
  115 passed.
  - *Test non-rég* : `test_seed_match.py` — cas 2 seeds proches en distance mais divergents sur
    `red_hue` (spread 50) → repli 1-seed au lieu de moyenne aveugle. Cas seeds cohérents (spread 2)
    → transplant inchangé (pas de régression).
  - *Valider* : `python -m pytest app/tests -q` vert.

- [x] **C3 — Trancher distance scène vs constante caméra.** ✅ (2026-07-19) : **décision = garder
  le k-NN scène-dépendant pour Calibration, ne pas remplacer par médiane/mode caméra.** Aucun
  changement de code (le comportement actuel était déjà le bon).
  Preuve (catalogue réel via Lr live, MCP `abelr.get_catalog_photos(include_develop=True)`,
  1057 photos, une seule caméra `ILCE-7M4`, un seul dossier « 2- Dernier soir Abreu ») : 270/1057
  photos ont des champs Calibration non nuls, regroupés en **77 vecteurs distincts** qui changent
  par blocs alignés sur des plages de frames séquentielles (ex. `SML03338`→`SML03360` :
  `(0,0,-5,10,5,5,15)` constant, puis `SML03361`→`SML03371` bascule à
  `(-25,0,-10,10,-10,40,-10)` — bloc à `BlueHue=40` nettement hors norme des blocs voisins
  (0–15), signature éclairage mixte isolé — puis `SML03372`→`SML03374` à
  `(5,0,-10,-10,-50,5,-5)`, etc.). Cette variation par blocs de scène/lumière, sur une **même**
  caméra et un **même** événement, contredit l'hypothèse « quasi constant par caméra » : le
  réglage Calibration chez cet utilisateur suit la scène (comme expo/wb/hsl), pas seulement le
  corps caméra. Le vecteur de distance k-NN scène (asshot_rg/bg, raw_median_l) reste donc
  pertinent pour Calibration — pas de repli médiane/mode global à coder. Script d'analyse
  jetable (non conservé, ad hoc sur le dump JSON du catalogue).
  - *Test non-rég* : aucun (décision = statu quo comportemental, `test_seed_match.py` existant
    reste la couverture valide du chemin k-NN Calibration, y compris la garde C2).
  - *Valider* : `python -m pytest app/tests -q` vert (115 passed, inchangé — aucun code touché).

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
