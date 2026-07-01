"""Worker Qt — décode et analyse les photos sur **GPU**, hors thread GUI.

Pour chaque photo : exposition (Y) + WB gray-world depuis le RAW, calculées via le
pipeline **GPU** (`core.gpu_raw` : bayer → ProPhoto linéaire → stats). Résultats mis en
cache SQLite (`SourceRAW`, clé = signature du RAW) ; au 2e passage, lecture cache sans
re-décoder. Émission incrémentale (photo par photo) pour rafraîchir le GUI.

Toutes les métriques sont en **échelle linéaire** (cf. `core.analysis`). Politique
**GPU-strict** : sans CUDA utilisable, le worker échoue avec un message clair.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QThread, Signal

from ..core import cache as cachemod, gpu, gpu_raw
from ..server.models import PhotoResult


@dataclass
class PhotoAnalysis:
    """Résultat d'analyse d'une seule photo (métriques linéaires)."""

    photo_id: str
    path: str
    source: str            # "gpu" | "cache" | "error"
    mean_luma: float       # luminance Y moyenne, linéaire 0-1
    median_luma: float
    clipped_highlights: float
    clipped_shadows: float
    wb_gain_rg: float      # gain gray-world g/r
    wb_gain_bg: float      # gain gray-world g/b
    error: Optional[str] = None


class AnalysisWorker(QThread):
    """Analyse une liste de PhotoResult (GPU + cache) ; émet un PhotoAnalysis par photo."""

    photo_done = Signal(object)   # PhotoAnalysis
    progress = Signal(int, int)   # (index 1-based, total)
    finished_all = Signal()
    failed = Signal(str)

    def __init__(self, photos: list[PhotoResult], half_size: bool = True) -> None:
        super().__init__()
        self._photos = photos
        self._half_size = half_size  # conservé pour compat. d'API (GPU = pleine résolution)

    def run(self) -> None:
        try:
            # GPU-strict : pas de repli CPU. Échec clair si CUDA absent.
            try:
                gpu.require_cuda()
            except Exception as exc:
                self.failed.emit(str(exc))
                return

            catalog_path = next(
                (p.catalog_path for p in self._photos if p.catalog_path), None
            )
            conn = None
            if catalog_path:
                try:
                    conn = cachemod.open_cache(catalog_path)
                except Exception:
                    conn = None

            try:
                total = len(self._photos)
                for i, photo in enumerate(self._photos, start=1):
                    self.progress.emit(i, total)
                    self.photo_done.emit(self._analyze_one(photo, conn))
            finally:
                if conn is not None:
                    conn.close()
            self.finished_all.emit()
        except Exception as exc:  # garde-fou : ne jamais tuer le thread silencieusement
            self.failed.emit(str(exc))

    def _analyze_one(self, photo: PhotoResult, conn) -> PhotoAnalysis:
        try:
            sig = cachemod.raw_signature(photo.path)
            cached = cachemod.get_source_raw(conn, photo.photo_id, sig) if conn else None
            if cached is not None and cached["exposure"] is not None:
                expo = cached["exposure"]
                return self._result(
                    photo, "cache", expo,
                    cached["grayworld_rg"], cached["grayworld_bg"],
                )

            res = gpu_raw.analyze_raw_gpu(photo.path)
            if res is None:
                raise RuntimeError("décodage GPU du RAW échoué")
            if conn is not None:
                try:
                    cachemod.put_source_raw(
                        conn, photo.photo_id, sig,
                        asshot_rg=res.asshot_rg, asshot_bg=res.asshot_bg,
                        exposure_global=res.exposure, exposure_sharp=res.exposure_sharp,
                        grayworld_global=(res.grayworld_rg, res.grayworld_bg),
                        grayworld_sharp=(res.grayworld_rg_sharp, res.grayworld_bg_sharp),
                        mask_sharp_frac=res.mask_sharp_frac,
                        tone=res.tone, bands=res.bands,
                    )
                except Exception:
                    pass
            return self._result(
                photo, "gpu", res.exposure, res.grayworld_rg, res.grayworld_bg
            )
        except Exception as exc:
            return PhotoAnalysis(
                photo_id=photo.photo_id, path=photo.path, source="error",
                mean_luma=0.0, median_luma=0.0, clipped_highlights=0.0,
                clipped_shadows=0.0, wb_gain_rg=0.0, wb_gain_bg=0.0, error=str(exc),
            )

    def _result(self, photo, source, expo, gw_rg, gw_bg) -> PhotoAnalysis:
        return PhotoAnalysis(
            photo_id=photo.photo_id,
            path=photo.path,
            source=source,
            mean_luma=expo.mean_luma,
            median_luma=expo.median_luma,
            clipped_highlights=expo.clipped_highlights,
            clipped_shadows=expo.clipped_shadows,
            wb_gain_rg=gw_rg,
            wb_gain_bg=gw_bg,
        )
