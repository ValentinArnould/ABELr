# PLAN — Cleanup & hardening (ARCHIVED 2026-07-19)

> Archived in favor of [`PLAN.md`](PLAN.md), which prioritizes the HSL/Calibration fixes
> (2026-07-19 audit). Steps 2-7 and the backlogs below are not abandoned — deferred, see §
> "Cleanup backlog (deferred)" of the new PLAN.md.

Executable roadmap **one step at a time**. Technical context: [`documentation/ARCHITECTURE.md`](documentation/ARCHITECTURE.md).
Working rules: [`CLAUDE.md`](CLAUDE.md).

## Execution rules (Sonnet 5)

1. Do **one step only** at a time, in order.
2. For each step: **implement the regression test BEFORE/WITH the change**.
3. Running `python -m app.main` is not required; validate with: `python -m pytest app/tests -q`
   (must stay **green**, including existing tests).
4. **Check `- [ ]` → `- [x]` only after a green test.** Never check off an unvalidated step.
5. Do not add a CPU fallback (GPU-strict). Do not touch live behavior without a test that covers it.
6. If a step breaks an existing test with no legitimate reason: stop, don't check it off, flag it.

Verified starting state (2026-07-05): 6 test files, ~40 tests, a single `@pytest.mark.gpu`,
**no pytest config** (the `gpu` marker registered only in `conftest.py`).

---

## Steps

- [x] **1 — Remove dead code.** *(done 2026-07-18, Fable 5 review — `test_no_dead_modules.py` green)*
  Remove `app/gui/analysis_worker.py` (`AnalysisWorker` never instantiated nor imported — verified).
  Note (Fable 5 review, Pass 0): `analysis_worker` is the only direct GUI importer of
  `gpu_raw`/`raw` — their live status hangs on the `gpu_schedule`/`embedded_jpeg` chain.
  Removing it kills no core module, but makes `gpu_schedule` the sole entry point into `gpu_raw`.
  - *Prior check*: `grep -rn "analysis_worker\|AnalysisWorker" app` returns only the definition.
  - *Regression test*: create `app/tests/test_no_dead_modules.py` which imports every module in
    `app/core/*` and `app/gui/*` (smoke import, except those requiring a Qt display →
    `importlib` with documented tolerance) and **asserts** that `app.gui.analysis_worker` no
    longer exists.
  - *Validate*: `python -m pytest app/tests -q` green.

- [ ] **2 — Pytest config.**
  Add a `pyproject.toml` at the root with `[tool.pytest.ini_options]`: `testpaths = ["app/tests"]`,
  `markers = ["gpu: GPU/CPU parity, skip if CUDA absent"]`. Keep the `gpu` skip from `conftest.py`.
  - *Test*: `python -m pytest -q` (no path) from the root correctly discovers the tests;
    `python -m pytest -q -m "not gpu"` works. No test broken.

- [ ] **3 — Anti-desync guard for the docs.**
  Create `app/tests/test_docs_consistency.py`: parse `CLAUDE.md` and `documentation/ARCHITECTURE.md`,
  and **assert** that (a) every cited `core/xxx.py` / `gui/xxx.py` actually exists on disk, (b)
  no removed file (`core/seeds.py`, `core/adjustments.py`, `core/prediction.py`,
  `gui/analysis_worker.py`) is presented as alive.
  - *Test = the test itself*: `python -m pytest app/tests/test_docs_consistency.py -q` green.

- [ ] **4 — Cover the untested live modules (pure functions only, no GPU or RAW).**
  Add tests for:
  - `core/exposure.py` — ΔEV from current L* to target L* (bound, sign, monotonicity).
  - `core/hsl.py` — per-band deltas vs. target; **saturation = reduction only** (never an increase).
  - `core/analysis.py` — `ev100()` / `ExposureStats` (known values).
  - `core/autocorrect.py` — `plan()` on synthetic `PhotoMeasure` objects (no disk/GPU access).
  - *Regression test*: new tests green; the ~40 existing tests unchanged.
  - *Note*: if a function isn't pure (depends on GPU/RAW), leave it out of scope and note it
    in the test — do not fabricate a mock that invents behavior.

- [ ] **5 — Logging hygiene.**
  In `app/gui/neutral_preview_worker.py`, replace `except Exception: pass` with a log
  (`logging` module, `warning`/`exception` level). If the anchor logic isn't already pure,
  isolate `_anchor_suspect` into a testable function.
  - *Regression test*: unit test of `_anchor_suspect` on edge cases (sound anchor vs. suspect) —
    numeric inputs, no Qt. `python -m pytest app/tests -q` green.

- [ ] **6 — QThread cleanup.**
  In `app/gui/main_window.py`, call `quit()` + `wait()` on the workers on close
  (`closeEvent`) to avoid orphaned threads.
  - *Test*: **limited, honest coverage** — smoke `python -c "import app.gui.main_window"` with
    no import crash (to be documented as such in the step). Real GUI validation → Appendix.

- [ ] **7 — Resync the final docs.**
  After 1-6, update `documentation/ARCHITECTURE.md` (remove `analysis_worker` from the map,
  §8) and `CLAUDE.md` (add the pytest config to the workflow if relevant).
  - *Test*: `test_docs_consistency` (step 3) stays green after the edit.

---

## Appendix — Lightroom-dependent validation (manual if no MCP)

Not automatable without Lr open (or without an exposed Lr MCP) — **outside the checkable boxes**:

- `requestJpegThumbnail` lock: 2nd Preview convergence ≈ 0; silent `_anchor_suspect` guard.
- Real e2e test: seeds → "Mark + analyze references" (`usable pool: N/N`) → select
  the rest → check axes → "Preview" (`seeds` mode, listed deltas) → "Apply" → re-Preview ≈ 0.
- Finishing the `photo_panel.py` / `analysis_panel.py` GUI stubs (previews, histograms, WB outliers).

## Remaining backlog (out of cleanup scope)

- **Camera calibration ("calib" axis)** — delivered 2026-07-19: k-NN transplant (ShadowTint,
  Red/Green/Blue Hue+Saturation) from the seeds, like Temperature/Tint, active in both
  reference modes (seeds and embedded — no measurable target from a JPEG, so always
  seed-sourced on the embedded side). `ANALYSIS_VERSION` bumped (`v6-calib-style-keys`,
  keys added to `_STYLE_KEYS`) + Lua `DEVELOP_KEYS` (+8). Tests: `test_autocorrect_calib.py`,
  aggregation in `test_seed_match.py`, clamp/dict in `test_autocorrect_helpers.py`.
  **Not validated in real Lightroom** (no Lr in this environment) — to be checked manually
  (appendix below): that `EnableCalibration` applies correctly, that the current seeds don't
  yet have hand-edited Calibration values (transplant will stay empty until a seed has
  been edited in the Camera Calibration panel).
- Artistic regime fallback: `core/regime.py` is no longer consulted by the live (k-NN) path. To
  be revalidated if matching turns out unstable on small seed pools.
- `core/image_source.py`: tool-only — remove it if the `tools/` that use it get archived.
- Perf: GPU + cache are in place, but Pass 2 (REVIEW_FABLE5.md) identifies structural
  losses (no CPU/GPU overlap, double rawpy open, undersized JPEG waves).
  Profile (`py-spy`/`torch.profiler`) then address G7 before considering Rust.

## Fable 5 review backlog (2026-07-18) — 🟠 items (detail: documentation/REVIEW_FABLE5.md, Pass 3)

2026-07-18 implementation: **all 🟠 items + most of the 🟡/⚪ delivered** (see
Pass 4 Journal of REVIEW_FABLE5.md). Tests: 78/78 green, GPU parity revalidated.

- [x] **G3 — Harden `Thumbnails.lua`** (L-01/L-02/L-03): `requestJpegThumbnail` return values
  retained, unique filename per call (generation), restore failures surfaced
  (`restore_error` up to the GUI).
- [x] **B-01 — Verify `_pending_ids` before Apply**: re-fetch selection, re-plan if there's a mismatch.
- [x] **G2 — exiftool argfile** (A-01/A-02): temp UTF-8 `-@ argfile` + `encoding="utf-8"`.
- [x] **G1 — Bump the shared `ANALYSIS_VERSION`** (DB-01/C-01): `DEVELOP_KEYS` 44→71 keys,
  `_STYLE_KEYS` fixed (real SDK names `SplitToning*`) + Texture/ToneCurve/Parametric,
  `cam_mul[G2]==0` guard — single bump `v5-style-keys-g2wb` (cache rebuild on 1st run).
- [x] **G7 — GPU scheduler overhaul** (P-01/P-02/P-03, +P-06): `process_combined_batch`
  (unified unpack, double-buffer, per-pipeline waves, reactive `empty_cache`, uint16 H2D).
  Parity revalidated (`validate_gpu_vs_libraw`: exposure corr 1.000, gray-world 0.996-0.9995).

Remaining (not addressed, scope decision):

- [ ] **G9 — GPU metrics micro-pass** (P-04/P-05): hoist hue/sat/chroma out of the dual,
  group the scalar syncs. **Bit-exact parity required** (otherwise bump `ANALYSIS_VERSION`) —
  to be done backed by profiling (`torch.profiler`).
- [ ] **P-10 — `_probe_chunk` decodes one at a time**: consistency more than perf (dominated by
  Lr rendering); batch via `analyze_render_blobs` at some point.
