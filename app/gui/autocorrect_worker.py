"""Worker Qt — correction auto (expo/WB/HSL) par photo, hors thread GUI.

Pipeline **GPU + cache** (hors thread GUI) :
1. **Embedded boîtier** (WB as-shot + tone/bandes du JPEG appareil) : cache SQLite
   (`InCameraJPEG` + `SourceRAW.asshot`, clé = signature du RAW) ; les manques sont
   déballés en CPU borné puis décodés sur **GPU** (nvJPEG) via `gpu_schedule`.
2. **Aperçu rendu courant** (tone/neutral/bandes) : cache `PreviewJPEG` (clé = signature
   du fichier d'aperçu) ; les manques sont décodés/mesurés sur **GPU**.
3. `autocorrect.plan(...)` → `PhotoAdjustment[]` + diagnostic.

Au 2e passage sur la même sélection (RAW/aperçus inchangés), tout vient du cache : aucun
décode. N'applique rien : émet le plan (le `main_window` décide Aperçu vs Appliquer).
Politique **GPU-strict** : sans CUDA utilisable, le worker échoue avec un message clair.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from PySide6.QtCore import QThread, Signal

from ..core import autocorrect, cache as cachemod, gpu, gpu_jpeg, gpu_schedule, measure, response
from ..core.autocorrect import PhotoMeasure, PlanDiagnostics
from ..core.embedded_jpeg import RawReference
from ..core.previews import PreviewIndex
from ..server.models import PhotoAdjustment, PhotoResult


@dataclass
class AutoCorrectResult:
    adjustments: list[PhotoAdjustment]
    diagnostics: PlanDiagnostics
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
    """Mesure la sélection (GPU + cache) et planifie la correction ; émet AutoCorrectResult."""

    finished_result = Signal(object)   # AutoCorrectResult
    progress = Signal(str)             # message d'étape
    failed = Signal(str)

    def __init__(
        self,
        photos: list[PhotoResult],
        axes: frozenset[str] = autocorrect.DEFAULT_AXES,
        forced_embedded: bool = False,
        thumbnail_paths: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._photos = photos
        self._axes = axes
        self._forced_embedded = forced_embedded
        self._thumbs = thumbnail_paths or {}

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
                refs = self._collect_embedded(photos, conn)
                analysis_by_id, channels, skipped_render = self._collect_renders(
                    photos, conn, idx
                )

                measures: list[PhotoMeasure] = []
                for p in photos:
                    ra = analysis_by_id.get(p.photo_id)
                    if ra is None:
                        continue
                    r = refs.get(p.photo_id) or RawReference(None, None, None, None)
                    measures.append(
                        PhotoMeasure(
                            photo_id=p.photo_id,
                            path=p.path,
                            current_develop=p.current_develop or {},
                            exif_camera=p.exif.camera if p.exif else None,
                            analysis=ra,
                            embedded_tone=r.embedded_tone,
                            embedded_bands=r.embedded_bands,
                            asshot_rg=r.asshot_rg,
                            asshot_bg=r.asshot_bg,
                        )
                    )
            finally:
                if idx is not None:
                    idx.close()
                if conn is not None:
                    conn.close()

            skipped = len(photos) - len(measures)
            if not measures:
                self.failed.emit(self._no_render_message(len(photos), channels, idx))
                return

            # Modèle de réponse calibré (caméra, profil le plus fréquent) si présent.
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
    # Étape 1 : embedded boîtier (asshot + tone/bandes) — cache + GPU
    # ------------------------------------------------------------------ #
    def _collect_embedded(self, photos, conn) -> dict[str, RawReference]:
        refs: dict[str, RawReference] = {}
        misses: list[PhotoResult] = []
        sig: dict[str, str] = {}
        for p in photos:
            s = cachemod.raw_signature(p.path)
            sig[p.photo_id] = s
            ic = cachemod.get_in_camera_jpeg(conn, p.photo_id, s) if conn else None
            sr = cachemod.get_source_raw(conn, p.photo_id, s) if conn else None
            if ic is not None and sr is not None:
                tone, bands = ic
                refs[p.photo_id] = RawReference(tone, bands, sr["asshot_rg"], sr["asshot_bg"])
            else:
                misses.append(p)

        if misses:
            self.progress.emit(f"Lecture RAW boîtier (GPU) — {len(misses)} photo(s)…")
            got = gpu_schedule.process_embedded_batch(
                [p.path for p in misses],
                progress=lambda d, t: self.progress.emit(f"RAW boîtier {d}/{t} (GPU)…"),
            )
            for p in misses:
                r = got.get(p.path) or RawReference(None, None, None, None)
                refs[p.photo_id] = r
                if conn is not None:
                    s = sig[p.photo_id]
                    if r.embedded_tone is not None and r.embedded_bands is not None:
                        _safe(lambda: cachemod.put_in_camera_jpeg(
                            conn, p.photo_id, s, tone=r.embedded_tone, bands=r.embedded_bands))
                    _safe(lambda: cachemod.put_source_raw(
                        conn, p.photo_id, s, asshot_rg=r.asshot_rg, asshot_bg=r.asshot_bg))
                    _safe(lambda: cachemod.put_picture(
                        conn, p.photo_id, path=p.path, catalog_path=p.catalog_path,
                        exif=(p.exif.model_dump() if p.exif else None),
                        current_develop=p.current_develop or {}))
        return refs

    # ------------------------------------------------------------------ #
    # Étape 2 : aperçu rendu courant (tone/neutral/bandes) — cache + GPU
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
            cached = cachemod.get_preview_jpeg(conn, p.photo_id, psig) if conn else None
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
