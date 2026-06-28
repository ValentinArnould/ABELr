"""Worker Qt — charge et analyse les photos hors du thread GUI.

Pour chaque photo : applique la politique Smart-Preview-puis-RAW
(`image_source.load_for_analysis`), calcule les métriques exposition + WB, et
émet un résultat incrémental pour que le GUI se mette à jour photo par photo.
Le décodage (Smart Preview JXL ou RAW) est lourd → jamais sur le thread Qt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QThread, Signal

from ..core import analysis, image_source
from ..core.previews import PreviewIndex
from ..server.models import PhotoResult


@dataclass
class PhotoAnalysis:
    """Résultat d'analyse d'une seule photo."""

    photo_id: str
    path: str
    source: str            # "smart_preview" | "raw"
    mean_luma: float
    median_luma: float
    clipped_highlights: float
    clipped_shadows: float
    wb_gain_rg: float      # gain gray-world g/r
    wb_gain_bg: float      # gain gray-world g/b
    error: Optional[str] = None


class AnalysisWorker(QThread):
    """Analyse une liste de PhotoResult ; émet un PhotoAnalysis par photo."""

    photo_done = Signal(object)   # PhotoAnalysis
    progress = Signal(int, int)   # (index 1-based, total)
    finished_all = Signal()
    failed = Signal(str)

    def __init__(
        self,
        photos: list[PhotoResult],
        catalog_path: Optional[str],
        half_size: bool = True,
    ) -> None:
        super().__init__()
        self._photos = photos
        self._catalog_path = catalog_path
        self._half_size = half_size

    def run(self) -> None:
        index: PreviewIndex | None = None
        try:
            # Un seul PreviewIndex (2 connexions SQLite) réutilisé pour tout le lot.
            if self._catalog_path:
                try:
                    index = PreviewIndex(self._catalog_path)
                except Exception:
                    index = None  # catalogue illisible → tout passe par le RAW

            total = len(self._photos)
            for i, photo in enumerate(self._photos, start=1):
                self.progress.emit(i, total)
                self.photo_done.emit(self._analyze_one(photo, index))
            self.finished_all.emit()
        except Exception as exc:  # garde-fou : ne jamais tuer le thread silencieusement
            self.failed.emit(str(exc))
        finally:
            if index is not None:
                index.close()

    def _analyze_one(
        self, photo: PhotoResult, index: PreviewIndex | None
    ) -> PhotoAnalysis:
        try:
            loaded = image_source.load_for_analysis(
                photo.photo_id, photo.path, index, half_size=self._half_size
            )
            stats = analysis.exposure_stats(loaded.rgb)
            gain_rg, gain_bg = analysis.gray_world_wb(loaded.rgb)
            return PhotoAnalysis(
                photo_id=photo.photo_id,
                path=photo.path,
                source=loaded.source,
                mean_luma=stats.mean_luma,
                median_luma=stats.median_luma,
                clipped_highlights=stats.clipped_highlights,
                clipped_shadows=stats.clipped_shadows,
                wb_gain_rg=gain_rg,
                wb_gain_bg=gain_bg,
            )
        except Exception as exc:
            return PhotoAnalysis(
                photo_id=photo.photo_id,
                path=photo.path,
                source="error",
                mean_luma=0.0,
                median_luma=0.0,
                clipped_highlights=0.0,
                clipped_shadows=0.0,
                wb_gain_rg=0.0,
                wb_gain_bg=0.0,
                error=str(exc),
            )
