# PLAN — Nettoyage & durcissement (ARCHIVÉ 2026-07-19)

> Archivé au profit de [`PLAN.md`](PLAN.md), qui priorise les corrections HSL/Calibration
> (audit 2026-07-19). Étapes 2-7 et backlogs ci-dessous non abandonnés — reportés, cf. §
> « Backlog nettoyage (reporté) » du nouveau PLAN.md.

Roadmap exécutable **une étape à la fois**. Contexte technique : [`documentation/ARCHITECTURE.md`](documentation/ARCHITECTURE.md).
Règles de travail : [`CLAUDE.md`](CLAUDE.md).

## Règles d'exécution (Sonnet 5)

1. Faire **une seule étape** à la fois, dans l'ordre.
2. Pour chaque étape : **implémenter le test de non-régression AVANT/AVEC le changement**.
3. Lancer `python -m app.main` n'est pas requis ; valider par : `python -m pytest app/tests -q`
   (doit rester **vert**, y compris les tests existants).
4. **Cocher `- [ ]` → `- [x]` uniquement après test vert.** Ne jamais cocher une étape non validée.
5. Ne pas ajouter de repli CPU (GPU-strict). Ne pas toucher au comportement live sans test qui le couvre.
6. Si une étape casse un test existant sans raison légitime : arrêter, ne pas cocher, signaler.

État de départ vérifié (2026-07-05) : 6 fichiers de test, ~40 tests, 1 seul `@pytest.mark.gpu`,
**aucune config pytest** (marker `gpu` enregistré seulement dans `conftest.py`).

---

## Étapes

- [x] **1 — Supprimer le code mort.** *(fait 2026-07-18, revue Fable 5 — `test_no_dead_modules.py` vert)*
  Supprimer `app/gui/analysis_worker.py` (`AnalysisWorker` jamais instancié ni importé — vérifié).
  Note (revue Fable 5, Passe 0) : `analysis_worker` est le seul importeur GUI direct de
  `gpu_raw`/`raw` — leur statut live tient par la chaîne `gpu_schedule`/`embedded_jpeg`.
  Sa suppression ne tue aucun module core, mais fait de `gpu_schedule` l'unique entrant de `gpu_raw`.
  - *Vérif préalable* : `grep -rn "analysis_worker\|AnalysisWorker" app` ne renvoie que la définition.
  - *Test non-rég* : créer `app/tests/test_no_dead_modules.py` qui importe chaque module de
    `app/core/*` et `app/gui/*` (smoke import, sauf ceux exigeant Qt display → `importlib` avec
    tolérance documentée) et **assert** qu'`app.gui.analysis_worker` n'existe plus.
  - *Valider* : `python -m pytest app/tests -q` vert.

- [ ] **2 — Config pytest.**
  Ajouter `pyproject.toml` à la racine avec `[tool.pytest.ini_options]` : `testpaths = ["app/tests"]`,
  `markers = ["gpu: parité GPU/CPU, skip si CUDA absent"]`. Garder le skip `gpu` de `conftest.py`.
  - *Test* : `python -m pytest -q` (sans chemin) depuis la racine découvre bien les tests ;
    `python -m pytest -q -m "not gpu"` fonctionne. Aucun test cassé.

- [ ] **3 — Garde anti-désync des docs.**
  Créer `app/tests/test_docs_consistency.py` : parse `CLAUDE.md` et `documentation/ARCHITECTURE.md`,
  et **assert** que (a) tout `core/xxx.py` / `gui/xxx.py` cité existe réellement sur disque, (b)
  aucun fichier supprimé (`core/seeds.py`, `core/adjustments.py`, `core/prediction.py`,
  `gui/analysis_worker.py`) n'est présenté comme vivant.
  - *Test = le test lui-même* : `python -m pytest app/tests/test_docs_consistency.py -q` vert.

- [ ] **4 — Couvrir les modules live non testés (fonctions pures uniquement, sans GPU ni RAW).**
  Ajouter des tests pour :
  - `core/exposure.py` — ΔEV depuis L* courant → L* cible (borne, signe, monotonie).
  - `core/hsl.py` — deltas par bande vs cible ; **saturation = réduction seule** (jamais d'augmentation).
  - `core/analysis.py` — `ev100()` / `ExposureStats` (valeurs connues).
  - `core/autocorrect.py` — `plan()` sur des `PhotoMeasure` synthétiques (aucun accès disque/GPU).
  - *Test non-rég* : nouveaux tests verts ; les ~40 tests existants inchangés.
  - *Note* : si une fonction n'est pas pure (dépend GPU/RAW), la laisser hors périmètre et le noter
    dans le test — ne pas fabriquer de mock qui invente un comportement.

- [ ] **5 — Hygiène logging.**
  Dans `app/gui/neutral_preview_worker.py`, remplacer les `except Exception: pass` par un log
  (module `logging`, niveau `warning`/`exception`). Si la logique d'ancre n'est pas déjà pure,
  isoler `_anchor_suspect` en fonction testable.
  - *Test non-rég* : unit test de `_anchor_suspect` sur cas limites (ancre saine vs suspecte) —
    entrées numériques, pas de Qt. `python -m pytest app/tests -q` vert.

- [ ] **6 — Cleanup QThread.**
  Dans `app/gui/main_window.py`, appeler `quit()` + `wait()` sur les workers à la fermeture
  (`closeEvent`) pour éviter les threads orphelins.
  - *Test* : **couverture limitée, honnête** — smoke `python -c "import app.gui.main_window"` sans
    crash d'import (à documenter comme tel dans l'étape). Validation GUI réelle → Annexe.

- [ ] **7 — Resync des docs finales.**
  Après 1-6, mettre à jour `documentation/ARCHITECTURE.md` (retirer `analysis_worker` de la carte,
  §8) et `CLAUDE.md` (ajouter la config pytest au workflow si pertinent).
  - *Test* : `test_docs_consistency` (étape 3) reste vert après édition.

---

## Annexe — Validation dépendante de Lightroom (manuelle si pas de MCP)

Non automatisable sans Lr ouvert (ou sans MCP Lr exposé) — **hors cases à cocher testables** :

- Verrou `requestJpegThumbnail` : convergence du 2ᵉ Aperçu ≈ 0 ; garde `_anchor_suspect` silencieuse.
- Test e2e réel : seeds → « Marquer + analyser références » (`pool exploitable : N/N`) → sélectionner
  le reste → cocher axes → « Aperçu » (mode `seeds`, deltas listés) → « Appliquer » → re-Aperçu ≈ 0.
- Finition des stubs GUI `photo_panel.py` / `analysis_panel.py` (aperçus, histogrammes, outliers WB).

## Backlog restant (hors périmètre nettoyage)

- **Étalonnage caméra (axe "calib")** — livré 2026-07-19 : transplant k-NN (ShadowTint,
  Red/Green/Blue Hue+Saturation) depuis les seeds, comme Temperature/Tint, actif dans les
  deux modes de référence (seeds et embedded — pas de cible mesurable depuis un JPEG,
  donc toujours seed-source côté embedded). `ANALYSIS_VERSION` bumpée (`v6-calib-style-keys`,
  clés ajoutées à `_STYLE_KEYS`) + `DEVELOP_KEYS` Lua (+8). Tests : `test_autocorrect_calib.py`,
  aggregation dans `test_seed_match.py`, clamp/dict dans `test_autocorrect_helpers.py`.
  **Non validé en Lightroom réel** (pas de Lr dans cet environnement) — à vérifier manuellement
  (annexe ci-dessous) : `EnableCalibration` s'applique bien, les seeds actuels n'ont pas encore
  de valeurs Étalonnage retouchées à la main (transplant restera vide tant qu'aucun seed n'a
  été édité dans le panneau Calibration caméra).
- Repli régime artistique : `core/regime.py` n'est plus consulté par le chemin live (k-NN). À
  revalider si le matching s'avère instable sur de petits pools de seeds.
- `core/image_source.py` : tool-only — le retirer si les `tools/` qui l'utilisent sont archivés.
- Perf : GPU + cache en place, mais la Passe 2 (REVIEW_FABLE5.md) identifie des pertes
  structurelles (pas de recouvrement CPU/GPU, double ouverture rawpy, vagues JPEG sous-dimensionnées).
  Profiler (`py-spy`/`torch.profiler`) puis traiter G7 avant d'envisager Rust.

## Backlog revue Fable 5 (2026-07-18) — items 🟠 (détail : documentation/REVIEW_FABLE5.md, Passe 3)

Implémentation 2026-07-18 : **tous les items 🟠 + l'essentiel des 🟡/⚪ livrés** (voir
Journal Passe 4 de REVIEW_FABLE5.md). Tests : 78/78 verts, parité GPU revalidée.

- [x] **G3 — Durcir `Thumbnails.lua`** (L-01/L-02/L-03) : retours de `requestJpegThumbnail`
  retenus, nom de fichier unique par appel (génération), échecs de restore remontés
  (`restore_error` jusqu'au GUI).
- [x] **B-01 — Vérifier `_pending_ids` avant Apply** : re-fetch sélection, re-planification si écart.
- [x] **G2 — exiftool argfile** (A-01/A-02) : `-@ argfile` temp UTF-8 + `encoding="utf-8"`.
- [x] **G1 — Bump `ANALYSIS_VERSION` commun** (DB-01/C-01) : `DEVELOP_KEYS` 44→71 clés,
  `_STYLE_KEYS` corrigées (noms SDK réels `SplitToning*`) + Texture/ToneCurve/Parametric,
  garde `cam_mul[G2]==0` — bump unique `v5-style-keys-g2wb` (rebuild cache au 1ᵉʳ run).
- [x] **G7 — Refonte scheduler GPU** (P-01/P-02/P-03, +P-06) : `process_combined_batch`
  (unpack unifié, double-buffer, vagues par pipeline, `empty_cache` réactif, H2D uint16).
  Parité revalidée (`validate_gpu_vs_libraw` : expo corr 1.000, gray-world 0.996-0.9995).

Restant (non traité, décision de périmètre) :

- [ ] **G9 — Micro-passe métriques GPU** (P-04/P-05) : hoister hue/sat/chroma du dual,
  regrouper les syncs scalaires. **Parité bit-exacte exigée** (sinon bump `ANALYSIS_VERSION`) —
  à faire avec profilage (`torch.profiler`) à l'appui.
- [ ] **P-10 — `_probe_chunk` décode une à une** : cohérence plus que perf (dominé par le
  rendu Lr) ; batcher via `analyze_render_blobs` à l'occasion.
