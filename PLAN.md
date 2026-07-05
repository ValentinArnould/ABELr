# PLAN — Nettoyage & durcissement

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

- [ ] **1 — Supprimer le code mort.**
  Supprimer `app/gui/analysis_worker.py` (`AnalysisWorker` jamais instancié ni importé — vérifié).
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

- [ ] **8 — (Conditionnelle) e2e piloté par MCP Lightroom.**
  **Prérequis** : le MCP Lightroom expose des outils dans la session. Un MCP ajouté ne charge ses
  outils qu'au **redémarrage** de Claude Code — vérifier au début (ex. via `/mcp` dans un terminal
  `claude`). Si absent → cette étape retombe dans l'Annexe (manuelle) ; **ne pas cocher**.
  - *Actions* : piloter Lr via le MCP — marquer des seeds → Analyser → Apply par axe → re-mesurer.
  - *Test non-rég* : **assert convergence** — le 2ᵉ delta doit être ≈ 0 (lève le verrou
    `requestJpegThumbnail`). Harness reproductible.
  - *Si le 2ᵉ delta n'est pas ≈ 0* : câbler le repli `RenderChannel.EXPORT` côté plugin
    (`Thumbnails.fetchProbeExport` + job `render_probe_export`) et re-tester jusqu'à convergence.

---

## Annexe — Validation dépendante de Lightroom (manuelle si pas de MCP)

Non automatisable sans Lr ouvert (ou sans MCP Lr exposé) — **hors cases à cocher testables** :

- Verrou `requestJpegThumbnail` : convergence du 2ᵉ Aperçu ≈ 0 ; garde `_anchor_suspect` silencieuse.
- Test e2e réel : seeds → « Marquer + analyser références » (`pool exploitable : N/N`) → sélectionner
  le reste → cocher axes → « Aperçu » (mode `seeds`, deltas listés) → « Appliquer » → re-Aperçu ≈ 0.
- Finition des stubs GUI `photo_panel.py` / `analysis_panel.py` (aperçus, histogrammes, outliers WB).

## Backlog restant (hors périmètre nettoyage)

- Repli régime artistique : `core/regime.py` n'est plus consulté par le chemin live (k-NN). À
  revalider si le matching s'avère instable sur de petits pools de seeds.
- `core/image_source.py` : tool-only — le retirer si les `tools/` qui l'utilisent sont archivés.
- Perf : parallélisation des séries 500-1000 déjà couverte par GPU + cache ; re-profiler avant tout Rust.
