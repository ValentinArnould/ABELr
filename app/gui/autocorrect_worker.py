"""Worker Qt — analyse (RAW + JPEG boîtier + aperçu, zone nette) et planification.

Pipeline **GPU + cache** (hors thread GUI) :
1. **RAW source** (tone+bandes zone nette, asshot WB, exposition, gray-world) :
   cache `SourceRAW` (clé = signature du RAW) ; manques → décodage GPU complet
   (`gpu_schedule.process_raw_batch`, demosaic inclus — coût dominant).
2. **JPEG boîtier** (tone+bandes zone nette) : cache `InCameraJPEG` (même clé) ;
   manques → décodage GPU (nvJPEG, `gpu_schedule.process_embedded_batch`).
3. **Aperçu rendu courant** (tone/neutral/bandes zone nette) : cache `PreviewJPEG`
   (clé = signature du fichier d'aperçu) ; manques → décodage GPU. En mode
   `force_fresh_preview=True` (Apply), le cache n'est **jamais lu** pour l'état
   courant (seulement écrit) — l'état mesuré doit être le plus frais possible
   pour éviter de recalculer un delta sur un rendu périmé.
4. Mode `analyze_only=True` ("Analyser sélection") : s'arrête après avoir peuplé
   le cache, n'appelle pas `autocorrect.plan`.
5. Sinon : construit le pool de seeds (`cache.list_seed_uuids` +
   `seed_match.build_seed_vector`) et appelle `autocorrect.plan(...)`.

Politique **GPU-strict** : sans CUDA utilisable, le worker échoue avec un message clair.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from PySide6.QtCore import QThread, Signal

from ..core import autocorrect, cache as cachemod, gpu, gpu_jpeg, gpu_schedule, measure
from ..core import response, seed_match
from ..core.autocorrect import PhotoMeasure, PlanDiagnostics
from ..core.previews import PreviewIndex
from ..server.models import PhotoAdjustment, PhotoResult


@dataclass
class AutoCorrectResult:
    adjustments: list[PhotoAdjustment]
    diagnostics: PlanDiagnostics | None
    n_measured: int
    n_skipped: int
    notes: list[str] = field(default_factory=list)


def _safe(fn) -> None:
    """Exécute une écriture cache en ignorant les erreurs (le cache ne doit jamais
    faire échouer l'analyse)."""
    try:
        fn()
    except Exception:
        pass


class AutoCorrectWorker(QThread):
    """Mesure la sélection (GPU + cache) et planifie/applique la correction."""

    finished_result = Signal(object)   # AutoCorrectResult
    progress = Signal(str)             # message d'étape
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

    def run(self) -> None:
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
            conn = None
            if catalog_path:
                try:
                    conn = cachemod.open_cache(catalog_path)
                except Exception:
                    conn = None
            idx = PreviewIndex(catalog_path) if catalog_path else None

            try:
                raw_by_id = self._collect_raw_source(photos, conn)
                embedded_by_id = self._collect_embedded_jpeg(photos, conn)
                render_by_id, channels, skipped_render = self._collect_renders(
                    photos, conn, idx
                )

                if self._analyze_only:
                    n = len(raw_by_id)
                    self.finished_result.emit(
                        AutoCorrectResult(
                            adjustments=[], diagnostics=None,
                            n_measured=n, n_skipped=len(photos) - n,
                            notes=[f"Analyse : {n}/{len(photos)} photo(s) (RAW+JPEG boîtier+aperçu)."],
                        )
                    )
                    return

                measures: list[PhotoMeasure] = []
                for p in photos:
                    ra = render_by_id.get(p.photo_id)
                    if ra is None:
                        continue
                    raw_d = raw_by_id.get(p.photo_id) or {}
                    emb = embedded_by_id.get(p.photo_id) or (None, None)
                    measures.append(
                        PhotoMeasure(
                            photo_id=p.photo_id,
                            path=p.path,
                            current_develop=p.current_develop or {},
                            exif_camera=p.exif.camera if p.exif else None,
                            analysis=ra,
                            is_seed=cachemod.is_seed(conn, p.photo_id) if conn else False,
                            raw_tone=raw_d.get("tone"),
                            embedded_tone=emb[0],
                            embedded_bands=emb[1],
                            asshot_rg=raw_d.get("asshot_rg"),
                            asshot_bg=raw_d.get("asshot_bg"),
                        )
                    )
            finally:
                if idx is not None:
                    idx.close()

            skipped = len(photos) - len(measures)
            if not measures:
                try:
                    msg = self._no_render_message(len(photos), channels, idx)
                finally:
                    if conn is not None:
                        conn.close()
                self.failed.emit(msg)
                return

            seed_pool = seed_match.build_seed_pool(conn) if conn else []
            if conn is not None:
                conn.close()

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
                )
            )
        except Exception as exc:  # garde-fou
            self.failed.emit(str(exc))

    # ------------------------------------------------------------------ #
    # Étape 1 : RAW source (tone+bandes zone nette, asshot, expo, gray-world)
    # ------------------------------------------------------------------ #
    def _collect_raw_source(self, photos, conn) -> dict[str, dict]:
        out: dict[str, dict] = {}
        misses: list[PhotoResult] = []
        sig: dict[str, str] = {}
        for p in photos:
            s = cachemod.raw_signature(p.path)
            sig[p.photo_id] = s
            cached = cachemod.get_source_raw(conn, p.photo_id, s) if conn else None
            if cached is not None and cached["tone"] is not None:
                out[p.photo_id] = cached
            else:
                misses.append(p)

        if misses:
            self.progress.emit(f"Lecture RAW source (GPU, zone nette) — {len(misses)} photo(s)…")
            got = gpu_schedule.process_raw_batch(
                [p.path for p in misses],
                progress=lambda d, t: self.progress.emit(f"RAW source {d}/{t} (GPU)…"),
            )
            for p in misses:
                r = got.get(p.path)
                if r is None:
                    continue
                out[p.photo_id] = {
                    "asshot_rg": r.asshot_rg, "asshot_bg": r.asshot_bg,
                    "tone": r.tone, "bands": r.bands,
                }
                if conn is not None:
                    s = sig[p.photo_id]
                    _safe(lambda: cachemod.put_source_raw(
                        conn, p.photo_id, s,
                        asshot_rg=r.asshot_rg, asshot_bg=r.asshot_bg,
                        exposure=r.exposure,
                        grayworld_rg=r.grayworld_rg, grayworld_bg=r.grayworld_bg,
                        tone=r.tone, bands=r.bands,
                    ))
                    _safe(lambda: cachemod.put_picture(
                        conn, p.photo_id, path=p.path, catalog_path=p.catalog_path,
                        exif=(p.exif.model_dump() if p.exif else None),
                        current_develop=p.current_develop or {}))
        return out

    # ------------------------------------------------------------------ #
    # Étape 2 : JPEG boîtier (tone+bandes zone nette) — cache + GPU
    # ------------------------------------------------------------------ #
    def _collect_embedded_jpeg(self, photos, conn) -> dict[str, tuple]:
        out: dict[str, tuple] = {}
        misses: list[PhotoResult] = []
        sig: dict[str, str] = {}
        for p in photos:
            s = cachemod.raw_signature(p.path)
            sig[p.photo_id] = s
            cached = cachemod.get_in_camera_jpeg(conn, p.photo_id, s) if conn else None
            if cached is not None:
                out[p.photo_id] = cached
            else:
                misses.append(p)

        if misses:
            self.progress.emit(f"Lecture JPEG boîtier (GPU) — {len(misses)} photo(s)…")
            got = gpu_schedule.process_embedded_batch(
                [p.path for p in misses],
                progress=lambda d, t: self.progress.emit(f"JPEG boîtier {d}/{t} (GPU)…"),
            )
            for p in misses:
                r = got.get(p.path)
                if r is None or r.embedded_tone is None:
                    out[p.photo_id] = (None, None)
                    continue
                out[p.photo_id] = (r.embedded_tone, r.embedded_bands)
                if conn is not None:
                    s = sig[p.photo_id]
                    _safe(lambda: cachemod.put_in_camera_jpeg(
                        conn, p.photo_id, s, tone=r.embedded_tone, bands=r.embedded_bands))
        return out

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
                progress=lambda d, t: self.progress.emit(f"Aperçu {d}/{t} (GPU)…"),
            )
            for pid, ra in decoded.items():
                if ra is None:
                    continue
                analysis_by_id[pid] = ra
                if conn is not None:
                    _safe(lambda: cachemod.put_preview_jpeg(
                        conn, pid, miss_sig[pid], analysis=ra))
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
