# ABELr — Lightroom Classic Plugin

Lightroom Classic plugin (Lua + Lr SDK) + external Python application for intelligent batch
editing. Core: **exposure / HSL / Calibration / White Balance per photo**, calibrated on
**seeds** (reference photos marked by hand) via k-NN matching on sharp-zone RAW analysis.

**Self-sufficient plugin**: `ABELr.lrplugin/` embeds everything — the Lua code *and* the
complete Python package (`ABELr.lrplugin/app/`), plus `launch.ps1`/`bootstrap.ps1`. Copying
this single folder to another machine is enough to install the plugin (Python 3.11+ + internet
required on first launch — `bootstrap.ps1` builds the venv and installs the dependencies, GPU
CUDA detected automatically otherwise falls back to CPU). The rest of the repo (`documentation/`,
`PLAN.md`…) is the dev repository, not a runtime dependency of the plugin.

## Where to read what

| File | For |
|---|---|
| [`documentation/ARCHITECTURE.md`](documentation/ARCHITECTURE.md) | **How the system works**: flow, module map (live/dead status), image pipeline, cache, GPU, communication |
| [`PLAN.md`](PLAN.md) | **Roadmap / status**: steps in progress, regression tests, backlog |
| [`documentation/lr15_sdk_api_reference.md`](documentation/lr15_sdk_api_reference.md) | **All Lua code**: imports, SDK APIs, Camera Raw 18 parameters, patterns, limitations. ⚠️ methods = unverified, confirm before use |
| [`documentation/project_overview.md`](documentation/project_overview.md) | Overall vision, historical decisions |
| [`ABELr.lrplugin/app/README.md`](ABELr.lrplugin/app/README.md) | Install / launch / `core/` structure |

> Before writing any Lua or looking up a develop parameter name: `lr15_sdk_api_reference.md`.
> Before claiming a module is used: the status map in ARCHITECTURE.md (§3) —
> several `core/` modules are tool-only or dead.

## Stack (detail: ARCHITECTURE.md § Stack)

| Layer | Tech |
|---|---|
| Plugin | Lua 5.1 + Adobe Lr Classic SDK 12+ |
| Server / GUI | Python 3.11+ · FastAPI · PySide6 (same process: server in a daemon thread, GUI on the main thread) |
| Image / GPU | rawpy · numpy · opencv · torch 2.6.0 + torchvision 0.21.0 (cu124, nvJPEG; **CPU fallback** if no CUDA GPU) |
| Analysis | scipy · scikit-learn · `exiftool` (external binary, outside pip) |

---

## Constraints never to violate

**Lua / SDK:**
- Lua 5.1: no `//`, `goto`, or `utf8` stdlib.
- Any catalog/develop write inside `catalog:withWriteAccessDo(...)`.
- Any blocking I/O inside `LrTasks.startAsyncTask`; `LrHttp.post` requires `LrFunctionContext.postAsyncTaskWithContext`.
- Windows paths via `LrPathUtils` — never concatenate `/`.
- SDK modules: `import 'LrXxx'`; plugin modules: `require`.
- No native JSON lib → embedded `Json.lua` (`Json.array(t)` forces a JSON array).
- `Collections.lua`, `Metadata.lua`, `PhotoLookup.lua`, `Presets.lua` (Phase 2, wired into
  `PollingLoop.lua`) contain SDK methods marked ⚠️ unverified in live Lr, in their own
  header — same rule as `lr15_sdk_api_reference.md`: confirm before extending/copying
  their usage.

**Python App:**
- **GPU-first, CPU fallback** (user decision, the plugin must run without an NVIDIA card):
  `app/core/gpu.py`: `device()` returns `cuda` if usable, otherwise `cpu` — **never raises**.
  The whole pipeline (`gpu_raw`, `gpu_jpeg`, `render_metrics_gpu`, `gpu_schedule`) routes its
  device through this call, so it switches automatically; GUI workers log a warning (not a
  failure) when running on CPU. `require_cuda()`/`GpuUnavailable` remain available for
  usages that explicitly want to require CUDA (`tools/calibrate_hsl_response.py`,
  `tools/validate_gpu_vs_libraw.py`, `tests/test_gpu_parity.py`) — do not use them as a
  default gate elsewhere. (Previous policy "GPU-strict, no CPU fallback" lifted — history
  in [[lr_gpu_cache_refactor]].)
- **Cache mandatory**: workers consult `cache` (SQLite, `app/core/cache.py`, 5 tables —
  `LightroomPicture`, `SourceRAW`, `InCameraJPEG`, `PreviewJPEG`, `NeutralPreviewJPEG`) first.
  `ANALYSIS_VERSION` is salted into the hashes → changing the measurement algorithm means
  bumping the constant (full rebuild, no migration; don't hardcode its value here, it moves
  on every bump — read `cache.py` if you need the current value).
- **`python -m app.main` runs without Lightroom**: the server starts on its own, the bridge
  just stays "disconnected". RAW decoding only requires the `.ARW` on disk, never the catalog
  nor Lr.

**Develop parameters = PV2012**: the real names carry the `2012` suffix (`Exposure2012`,
`Highlights2012`…). `WhiteBalance='Custom'` is required for `Temperature`/`Tint` to take effect.
`WhiteBalance='Custom'` also serves as a historical marker on the App side.

---

## Communication (detail: ARCHITECTURE.md §2 — ⚠️ that §2 is behind this section, trust this one)

**Plugin = ALWAYS HTTP client. App = ALWAYS server (`127.0.0.1:5000`).** The App never
pushes: it drops a job into `job_queue`, the plugin picks it up by polling (`GET /jobs/pending`,
300 ms) and returns it via `POST /jobs/{id}/result`.

Jobs (14 — source of truth: `JobType` enum in `app/server/models.py` + `dispatch()` in
`PollingLoop.lua`, keep them in sync on any addition):
- Base: `test`, `get_selected_photos`, `get_catalog_photos`, `get_thumbnails`, `render_probe`, `apply_adjustments`
- Metadata: `set_rating`, `set_flag_color`, `set_keywords`
- Collections: `list_collections`, `create_collection`, `add_to_collection`
- Presets: `list_develop_presets`, `apply_develop_preset`

```json
{ "job_id": "uuid", "type": "apply_adjustments",
  "payload": { "adjustments": [ { "photo_id": "...", "develop": {
      "WhiteBalance": "Custom", "Temperature": 5650, "Tint": -5, "Exposure2012": 0.35 } } ] } }
```

**Second channel — MCP (`app/mcp/server.py` + `tools.py`, mounted on `/mcp` in `app/server/api.py`)**:
exposes the `job_queue` above as 15 MCP tools for Claude Code itself (introspection,
reading, writing, metadata/collections/presets), registered in [`.mcp.json`](.mcp.json)
(server `abelr`, `http://127.0.0.1:5000/mcp`). Used to drive live Lr during dev without
writing a script. Requires `python -m app.main` running; tools that depend on the bridge time
out cleanly if the Lr plugin isn't connected (no crash).

---

## Development workflow

**Lua plugin:** edit in `ABELr.lrplugin/` → Lr: *File > Plug-in Manager* > Reload → test via
*Library > Plug-in Extras* → logs via `Utils.logf` in *Help > Lua Console*.

**Python App:** all commands are run from `ABELr.lrplugin/` (the plugin is the root of the
Python package since the self-sufficient rework — `app/` is no longer at the repo root).
`python -m app.main` (or `launch.ps1`, which chains `bootstrap.ps1` automatically if `app/.venv`
is missing — first launch). Venv expected at `app/.venv` (relative to `ABELr.lrplugin/`).
Endpoints: `curl http://127.0.0.1:5000/health`. Mock without Lr: `python -m app.tools.mock_plugin`.
Drive live Lr without writing a script: `abelr` MCP tools (see § Communication) — the app
must be running.

**Unit tests (pure functions, no GPU or RAW) — from `ABELr.lrplugin/`:**
```
python -m pytest app/tests -q            # all
python -m pytest app/tests -q -m "not gpu"   # excludes GPU parity (skipped if CUDA is absent)
```

**Fastest path to validate an algorithm**: call `core/` directly on real `.ARW` files
(`raw.load_linear`, `analysis.gray_world_wb`, `gpu_raw.analyze_raw_gpu`, `seed_match.k_nearest`)
without going through the server or the GUI — see `tools/`.

**Installing on another machine:** copy only the `ABELr.lrplugin/` folder
(no need for the rest of the repo) → install it as an Lr plug-in → the *Start/connect
the application* menu triggers `bootstrap.ps1` on first launch (Python 3.11+ must be on the
PATH, internet connection required for the duration of the download — torch CUDA ~2.5 GB if
an NVIDIA GPU is detected via `nvidia-smi`, otherwise CPU build ~250 MB). `exiftool` stays
separate (external binary, system PATH or `ABELr.lrplugin/bin/exiftool.exe` if bundled
manually — its absence is not blocking).

---

## Naming conventions

| Context | Convention |
|---|---|
| Lua files | `PascalCase.lua` · functions/locals `camelCase` · constants `UPPER_SNAKE_CASE` |
| Python files | `snake_case.py` · classes `PascalCase` · functions/vars `snake_case` |
| Exchanged JSON keys | `snake_case` |
| Lr SDK parameter names in JSON | `PascalCase` (identical to the SDK) |
