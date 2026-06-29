"""Worker Qt — décode et analyse les photos hors du thread GUI.

Pour chaque photo : décode le RAW en ProPhoto linéaire
(`image_source.load_for_analysis`), calcule les métriques exposition + WB, et émet
un résultat incrémental pour que le GUI se mette à jour photo par photo. Le décodage
RAW est lourd (~1 s/photo) → jamais sur le thread Qt.

Toutes les métriques sont en **échelle linéaire** (cf. `core.analysis`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QThread, Signal

from ..core import analysis, image_source
from ..server.models import PhotoResult


@dataclass
class PhotoAnalysis:
    """Résultat d'analyse d'une seule photo (métriques linéaires)."""

    photo_id: str
    path: str
    source: str            # "raw" | "error"
    mean_luma: float       # luminance Y moyenne, linéaire 0-1
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

    def __init__(self, photos: list[PhotoResult], half_size: bool = True) -> None:
        super().__init__()
        self._photos = photos
        self._half_size = half_size

    def run(self) -> None:
        try:
            total = len(self._photos)
            for i, photo in enumerate(self._photos, start=1):
                self.progress.emit(i, total)
                self.photo_done.emit(self._analyze_one(photo))
            self.finished_all.emit()
        except Exception as exc:  # garde-fou : ne jamais tuer le thread silencieusement
            self.failed.emit(str(exc))

    def _analyze_one(self, photo: PhotoResult) -> PhotoAnalysis:
        try:
            loaded = image_source.load_for_analysis(photo.path, half_size=self._half_size)
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
