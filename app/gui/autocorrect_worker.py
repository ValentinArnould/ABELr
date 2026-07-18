"""Worker Qt — analyse (RAW + JPEG boîtier + aperçu/ancre neutre) et planification.

Pipeline **GPU + cache** (hors thread GUI) :
1+2. **RAW source + JPEG boîtier** en un passage fusionné (revue Fable 5 G7) :
   caches `SourceRAW`/`InCameraJPEG` (même clé = signature du RAW) ; manques →
   `gpu_schedule.process_combined_batch` (une ouverture rawpy par photo, demosaic
   GPU + nvJPEG, double-buffer CPU/GPU).
3. Mesure de l'état de référence, **selon le mode** :
   - **seeds** : aperçu rendu courant (cache `PreviewJPEG` ; en
     `force_fresh_preview=True` le cache n'est jamais lu, seulement écrit).
   - **embedded** : **ancre neutre** (`ensure_neutral_previews` — cache
     `NeutralPreviewJPEG`, jobs plugin `render_probe` pour les manques). Aucune
     mesure du rendu courant : les valeurs planifiées sont absolues (idempotentes)
     et insensibles à la fraîcheur des aperçus Lr.
4. Mode `analyze_only=True` ("Marquer + analyser") : s'arrête après avoir peuplé
   le cache (RAW + JPEG boîtier + aperçu), n'appelle pas `autocorrect.plan`.
5. Sinon : pool de seeds → `autocorrect.plan(...)` (le biais de profil embedded a
   été supprimé — décision « biais ignoré », revue Fable 5 DB-06).

Politique **GPU-strict** : sans CUDA utilisable, le worker échoue avec un message clair.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from PySide6.QtCore import QThread, Signal

from ..core import analysis as analysismod
from ..core import autocorrect, cache as cachemod, exif_profile, gpu, gpu_jpeg, gpu_schedule, measure
from ..core import response, seed_match
from ..core.autocorrect import PhotoMeasure, PlanDiagnostics
from ..core.previews import PreviewIndex
from ..server.models import PhotoAdjustment, PhotoResult
from .neutral_preview_worker import ensure_neutral_previews


@dataclass
class AutoCorrectResult:
    adjustments: list[PhotoAdjustment]
    diagnostics: PlanDiagnostics | None
    n_measured: int
    n_skipped: int
    notes: list[str] = field(default_factory=list)
    # Seeds marqués en DB vs réellement exploitables (analyse RAW présente).
    # marked > usable ⇒ repli embedded silencieux à signaler côté GUI.
    seeds_marked: int = 0
    seeds_usable: int = 0


def _safe(fn) -> None:
    """Exécute une écriture cache en ignorant les erreurs (le cache ne doit jamais
    faire échouer l'analyse)."""
    try:
        fn()
    except Exception:
        pass


def _compute_deltas(raw_dict: dict | None, jpeg_sharp) -> dict:
    """Deltas JPEG boîtier ↔ RAW (zone nette) = transformation appliquée par le profil.

    - `delta_luma_median` : L* médian JPEG − L* médian RAW (rendu neutre) → lift tonal
      du profil créatif.
    - `delta_wb_cast_a/b` : cast a*/b* mesuré sur les neutres du JPEG (le RAW as-shot
      sert de référence ≈ neutre) → teinte cuite par le profil.
    - `delta_hsl` : par bande, écart chroma/sat/hue/L* JPEG − RAW.
    Retourne un dict de kwargs prêt pour `put_in_camera_jpeg` (valeurs None si non
    calculables)."""
    out = {
        "delta_luma_median": None, "delta_wb_cast_a": None,
        "delta_wb_cast_b": None, "delta_hsl": None,
    }
    if jpeg_sharp is None:
        return out
    if jpeg_sharp.neutral is not None:
        out["delta_wb_cast_a"] = jpeg_sharp.neutral.a_bias
        out["delta_wb_cast_b"] = jpeg_sharp.neutral.b_bias
    raw_tone = (raw_dict or {}).get("tone")
    if raw_tone is not None and jpeg_sharp.tone is not None:
        out["delta_luma_median"] = jpeg_sharp.tone.median_l - raw_tone.median_l
    raw_bands = {b.name: b for b in ((raw_dict or {}).get("bands") or [])}
    if jpeg_sharp.bands and raw_bands:
        deltas = []
        for jb in jpeg_sharp.bands:
            rb = raw_bands.get(jb.name)
            if rb is None:
                continue
            deltas.append({
                "name": jb.name,
                "dchroma": jb.median_chroma - rb.median_chroma,
                "dsat": jb.median_sat - rb.median_sat,
                "dhue": jb.median_hue - rb.median_hue,
                "dl": jb.median_l - rb.median_l,
            })
        out["delta_hsl"] = deltas or None
    return out


class AutoCorrectWorker(QThread):
    """Mesure la sélection (GPU + cache) et planifie/applique la correction."""

    finished_result = Signal(object)   # AutoCorrectResult
    progress = Signal(str)             # message d'étape
    progress_count = Signal(int, int)  # (fait, total) d'une étape → barre déterminée
    failed = Signal(str)

    def __init__(
        self,
        photos: list[PhotoResult],
        axes: frozenset[str] = autocorrect.DEFAULT_AXES,
        forced_embedded: bool = False,
        thumbnail_paths: dict[str, str] | None = None,
        analyze_only: bool = False,
        force_fresh_preview: bool = False,
    ) -> None:
        super().__init__()
        self._photos = photos
        self._axes = axes
        self._forced_embedded = forced_embedded
        self._thumbs = thumbnail_paths or {}
        self._analyze_only = analyze_only
        self._force_fresh_preview = force_fresh_preview
        self._profile_cache: dict[str, str | None] = {}  # path → profil créatif boîtier

    def _batch_progress(self, label: str):
        """Callback `(done, total)` d'une étape GPU → émet le texte ET les compteurs
        (ces derniers pilotent la barre de chargement déterminée côté GUI)."""
        def cb(done: int, total: int) -> None:
            self.progress.emit(f"{label} {done}/{total} (GPU)…")
            self.progress_count.emit(done, total)
        return cb

    def _profiles(self, paths: list[str]) -> dict[str, str | None]:
        """Profil créatif boîtier pour un lot (exiftool, batché, mémoïsé sur le worker).

        Une seule invocation exiftool par lot de chemins non encore lus ; robuste à
        l'absence d'exiftool (valeurs None)."""
        todo = [p for p in paths if p not in self._profile_cache]
        if todo:
            got = exif_profile.read_capture_profiles(todo)
            for p in todo:
                self._profile_cache[p] = got.get(p)
        return {p: self._profile_cache.get(p) for p in paths}

    def run(self) -> None:
        conn = None  # fermée dans le finally — y compris sur exception (revue Fable 5 B-04)
        try:
            photos = self._photos
            if not photos:
                self.failed.emit("Aucune photo sélectionnée.")
                return

            # Politique GPU-strict : pas de repli CPU. Échec clair si CUDA absent.
            try:
                gpu.require_cuda()
            except Exception as exc:
                self.failed.emit(str(exc))
                return

            catalog_path = next((p.catalog_path for p in photos if p.catalog_path), None)
            if catalog_path:
                try:
                    conn = cachemod.open_cache(catalog_path)
                except Exception:
                    conn = None
            idx = PreviewIndex(catalog_path) if catalog_path else None

            try:
                raw_by_id, embedded_by_id = self._collect_raw_and_embedded(photos, conn)

                if self._analyze_only:
                    # Peuple aussi le cache d'aperçus (utile aux seeds futurs).
                    self._collect_renders(photos, conn, idx)
                    n = len(raw_by_id)
                    marked = len(cachemod.list_seed_uuids(conn)) if conn else 0
                    usable = len(seed_match.build_seed_pool(conn)) if conn else 0
                    self.finished_result.emit(
                        AutoCorrectResult(
                            adjustments=[], diagnostics=None,
                            n_measured=n, n_skipped=len(photos) - n,
                            notes=[f"Analyse : {n}/{len(photos)} photo(s) (RAW+JPEG boîtier+aperçu)."],
                            seeds_marked=marked, seeds_usable=usable,
                        )
                    )
                    return

                seeds_marked = len(cachemod.list_seed_uuids(conn)) if conn else 0
                seed_pool = seed_match.build_seed_pool(conn) if conn else []
                mode_embedded = self._forced_embedded or not seed_pool

                notes: list[str] = []
                channels: Counter[str] = Counter()
                measures: list[PhotoMeasure] = []

                if mode_embedded:
                    # Ancre neutre (cache hash_style, jobs render_probe pour les
                    # manques) — AUCUNE mesure du rendu courant.
                    neutral_by_id, n_refreshed = ensure_neutral_previews(
                        photos, conn, progress=self.progress.emit,
                        progress_count=self.progress_count.emit,
                    )
                    if n_refreshed:
                        notes.append(f"{n_refreshed} ancre(s) neutre(s) recalibrée(s) via Lightroom.")
                    for p in photos:
                        nd = neutral_by_id.get(p.photo_id)
                        emb = embedded_by_id.get(p.photo_id) or (None, None)
                        if nd is None or (emb[0] is None and emb[1] is None):
                            continue
                        raw_d = raw_by_id.get(p.photo_id) or {}
                        measures.append(
                            PhotoMeasure(
                                photo_id=p.photo_id,
                                path=p.path,
                                current_develop=p.current_develop or {},
                                exif_camera=p.exif.camera if p.exif else None,
                                analysis=None,
                                is_seed=cachemod.is_seed(conn, p.photo_id) if conn else False,
                                raw_tone=raw_d.get("tone"),
                                embedded_sharp=emb[0],
                                embedded_global=emb[1],
                                neutral_sharp=nd.get("sharp"),
                                neutral_global=nd.get("glob"),
                                neutral_asshot_temp=nd.get("asshot_temp"),
                                neutral_asshot_tint=nd.get("asshot_tint"),
                                hash_style=cachemod.style_hash(p.current_develop or {}),
                                asshot_rg=raw_d.get("asshot_rg"),
                                asshot_bg=raw_d.get("asshot_bg"),
                                profile_capture=raw_d.get("profile_capture"),
                                ev100=raw_d.get("ev100"),
                            )
                        )
                else:
                    render_by_id, channels, _skipped_render = self._collect_renders(
                        photos, conn, idx
                    )
                    for p in photos:
                        ra = render_by_id.get(p.photo_id)
                        if ra is None:
                            continue
                        raw_d = raw_by_id.get(p.photo_id) or {}
                        measures.append(
                            PhotoMeasure(
                                photo_id=p.photo_id,
                                path=p.path,
                                current_develop=p.current_develop or {},
                                exif_camera=p.exif.camera if p.exif else None,
                                analysis=ra,
                                is_seed=cachemod.is_seed(conn, p.photo_id) if conn else False,
                                raw_tone=raw_d.get("tone"),
                                asshot_rg=raw_d.get("asshot_rg"),
                                asshot_bg=raw_d.get("asshot_bg"),
                                profile_capture=raw_d.get("profile_capture"),
                                ev100=raw_d.get("ev100"),
                            )
                        )
            finally:
                if idx is not None:
                    idx.close()

            skipped = len(photos) - len(measures)
            if not measures:
                if mode_embedded:
                    self.failed.emit(
                        f"Aucune photo mesurable sur {len(photos)} : ancre neutre ou "
                        f"JPEG boîtier manquant(s). Vérifiez le pont plugin (jobs "
                        f"render_probe) et que les RAW contiennent un JPEG embarqué."
                    )
                else:
                    self.failed.emit(self._no_render_message(len(photos), channels, idx))
                return

            camera = next((m.exif_camera for m in measures if m.exif_camera), None)
            profiles = Counter(
                m.current_develop.get("CameraProfile") for m in measures
                if m.current_develop.get("CameraProfile")
            )
            profile = profiles.most_common(1)[0][0] if profiles else None
            model = response.load(camera, profile)

            self.progress.emit("Planification des corrections…")
            adjustments, diag = autocorrect.plan(
                measures,
                axes=self._axes,
                forced_embedded=self._forced_embedded,
                model=model,
                camera=camera,
                seed_pool=seed_pool,
            )
            self.finished_result.emit(
                AutoCorrectResult(
                    adjustments=adjustments,
                    diagnostics=diag,
                    n_measured=len(measures),
                    n_skipped=skipped,
                    notes=notes,
                    seeds_marked=seeds_marked,
                    seeds_usable=len(seed_pool),
                )
            )
        except Exception as exc:  # garde-fou
            self.failed.emit(str(exc))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------ #
    # Étapes 1+2 fusionnées : RAW source + JPEG boîtier (revue Fable 5 G7/P-02)
    # ------------------------------------------------------------------ #
    def _collect_raw_and_embedded(self, photos, conn) -> tuple[dict[str, dict], dict[str, tuple]]:
        """RAW (tone+bandes zone nette, asshot, expo, gray-world) + cibles JPEG
        boîtier {uuid: (sharp, glob)} en UN passage GPU.

        Les deux caches partagent la même clé de fraîcheur (`raw_signature`) : une
        photo manquante des deux côtés n'ouvre le conteneur ARW qu'une seule fois
        (`gpu_schedule.process_combined_batch`, unpack unifié + double-buffer).
        Écrit `SourceRAW`/`LightroomPicture`/`InCameraJPEG` avec les **deltas
        précalculés** vs RAW, en UNE transaction (P-07)."""
        raw_out: dict[str, dict] = {}
        emb_out: dict[str, tuple] = {}
        raw_misses: list[PhotoResult] = []
        emb_misses: list[PhotoResult] = []
        sig: dict[str, str] = {}
        for p in photos:
            s = cachemod.raw_signature(p.path)
            sig[p.photo_id] = s
            cached = cachemod.get_source_raw(conn, p.photo_id, s) if conn else None
            if cached is not None and cached["tone"] is not None:
                raw_out[p.photo_id] = cached
            else:
                raw_misses.append(p)
            cached_j = cachemod.get_in_camera_jpeg(conn, p.photo_id, s) if conn else None
            if cached_j is not None:
                emb_out[p.photo_id] = (cached_j["sharp"], cached_j["global"])
            else:
                emb_misses.append(p)

        if not raw_misses and not emb_misses:
            return raw_out, emb_out

        self.progress.emit(
            f"Lecture RAW + JPEG boîtier (GPU) — {len(raw_misses)} RAW / "
            f"{len(emb_misses)} JPEG manquant(s)…"
        )
        got_raw, got_emb = gpu_schedule.process_combined_batch(
            [p.path for p in raw_misses],
            [p.path for p in emb_misses],
            progress=self._batch_progress("RAW + JPEG boîtier"),
        )
        miss_paths = list(dict.fromkeys(p.path for p in raw_misses + emb_misses))
        profiles = self._profiles(miss_paths)

        for p in raw_misses:
            r = got_raw.get(p.path)
            if r is None:
                continue
            prof = profiles.get(p.path)
            ev = analysismod.ev100(
                p.exif.iso if p.exif else None,
                p.exif.aperture if p.exif else None,
                p.exif.shutter_speed if p.exif else None,
            )
            raw_out[p.photo_id] = {
                "asshot_rg": r.asshot_rg, "asshot_bg": r.asshot_bg,
                "tone": r.tone, "bands": r.bands,
                "ev100": ev, "profile_capture": prof,
            }
            if conn is not None:
                s = sig[p.photo_id]
                _safe(lambda r=r, s=s, p=p, ev=ev, prof=prof: cachemod.put_source_raw(
                    conn, p.photo_id, s,
                    asshot_rg=r.asshot_rg, asshot_bg=r.asshot_bg,
                    exposure_global=r.exposure, exposure_sharp=r.exposure_sharp,
                    grayworld_global=(r.grayworld_rg, r.grayworld_bg),
                    grayworld_sharp=(r.grayworld_rg_sharp, r.grayworld_bg_sharp),
                    mask_sharp_frac=r.mask_sharp_frac, ev100=ev, profile_capture=prof,
                    tone=r.tone, bands=r.bands, commit=False,
                ))
                _safe(lambda p=p, prof=prof: cachemod.put_picture(
                    conn, p.photo_id, path=p.path, catalog_path=p.catalog_path,
                    exif=(p.exif.model_dump() if p.exif else None),
                    current_develop=p.current_develop or {}, profile_capture=prof,
                    commit=False))

        for p in emb_misses:
            r = got_emb.get(p.path)
            if r is None or r.sharp is None:
                emb_out[p.photo_id] = (None, None)
                continue
            emb_out[p.photo_id] = (r.sharp, r.glob)
            if conn is not None:
                s = sig[p.photo_id]
                prof = profiles.get(p.path)
                deltas = _compute_deltas(raw_out.get(p.photo_id), r.sharp)
                _safe(lambda r=r, s=s, p=p, prof=prof, deltas=deltas:
                      cachemod.put_in_camera_jpeg(
                          conn, p.photo_id, s,
                          sharp=r.sharp, glob=r.glob,
                          mask_sharp_frac=r.mask_sharp_frac, profile_capture=prof,
                          commit=False, **deltas))

        # Un seul commit pour tout le passage (revue Fable 5 P-07) : évite
        # ~2-3 commits/photo (churn WAL) sur les gros lots.
        if conn is not None:
            _safe(conn.commit)
        return raw_out, emb_out

    # ------------------------------------------------------------------ #
    # Étape 3 : aperçu rendu courant (tone/neutral/bandes zone nette) — cache + GPU
    # ------------------------------------------------------------------ #
    def _collect_renders(self, photos, conn, idx):
        analysis_by_id: dict[str, object] = {}
        channels: Counter[str] = Counter()
        misses: list[tuple[str, bytes]] = []
        miss_sig: dict[str, str] = {}
        skipped = 0
        for p in photos:
            path, ch = measure.resolve_render_path(
                thumbnail_path=self._thumbs.get(p.photo_id),
                preview_index=idx,
                id_global=p.photo_id,
            )
            channels[ch.value] += 1
            if path is None:
                skipped += 1
                continue
            psig = cachemod.raw_signature(path)
            cached = (
                None if self._force_fresh_preview
                else (cachemod.get_preview_jpeg(conn, p.photo_id, psig) if conn else None)
            )
            if cached is not None:
                analysis_by_id[p.photo_id] = cached
                continue
            try:
                stream = gpu_jpeg.extract_jpeg_stream(path.read_bytes())
            except OSError:
                stream = None
            if stream is None:
                skipped += 1
                continue
            misses.append((p.photo_id, stream))
            miss_sig[p.photo_id] = psig

        if misses:
            self.progress.emit(f"Aperçus rendus (GPU) — {len(misses)} photo(s)…")
            decoded = gpu_schedule.analyze_render_blobs(
                misses,
                progress=self._batch_progress("Aperçu"),
            )
            for pid, dual in decoded.items():
                if dual is None:
                    continue
                analysis_by_id[pid] = dual.sharp  # état courant = zone nette
                if conn is not None:
                    _safe(lambda pid=pid, dual=dual: cachemod.put_preview_jpeg(
                        conn, pid, miss_sig[pid],
                        sharp=dual.sharp, glob=dual.glob,
                        mask_sharp_frac=dual.mask_sharp_frac, commit=False))
            if conn is not None:
                _safe(conn.commit)  # P-07 : un commit par étape
        return analysis_by_id, channels, skipped

    # ------------------------------------------------------------------ #
    # Message d'échec « aucun rendu » — diagnostic précis
    # ------------------------------------------------------------------ #
    def _no_render_message(self, n_photos: int, channels: Counter, idx) -> str:
        cause = (
            "aucun catalog_path reçu du plugin → impossible de localiser Previews.lrdata"
            if idx is None
            else "aucun aperçu trouvé dans Previews.lrdata pour la sélection"
        )
        return (
            f"Aucun rendu mesurable sur {n_photos} photo(s) (canaux: {dict(channels)}). "
            f"Cause probable : {cause}. Miniatures fournies par le plugin : "
            f"{len(self._thumbs)} (canal non câblé côté GUI). Remède : générez les "
            f"aperçus standard/1:1 dans Lightroom (Bibliothèque > Aperçus > Générer "
            f"les aperçus), puis relancez."
        )
