"""Worker Qt — calibre le modèle WB sur les seeds et planifie les corrections.

Hors thread GUI car il décode l'as-shot WB de chaque photo (ouverture RAW). Flux :
seeds → `wb_model.calibrate` → `regime.detect` → `seeds.plan_adjustments` pour les
non-seeds. Émet un rapport (calibration + régime) et la liste des PhotoAdjustment
prêts à appliquer via le job apply_adjustments.

Note perf : `plan_adjustments` ouvre chaque RAW non-seed pour lire l'as-shot
(métadonnée, pas de décodage pixel) — acceptable en v1, à paralléliser pour les
grosses séries (backlog).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from ..core import regime, seeds, wb_model
from ..core.regime import Regime, RegimeReport
from ..core.wb_model import WBCalibration
from ..server.models import PhotoAdjustment, PhotoResult


@dataclass
class CalibrationResult:
    calibration: WBCalibration
    regime: RegimeReport
    adjustments: list[PhotoAdjustment]
    n_seeds: int
    n_planned: int
    used_model: bool = True  # False = régime artistique, Temperature fixe appliquée


class CalibrateWorker(QThread):
    """Calibre + planifie ; émet CalibrationResult ou une erreur."""

    finished_result = Signal(object)  # CalibrationResult
    failed = Signal(str)

    def __init__(
        self,
        photos: list[PhotoResult],
        seed_ids: set[str] | None = None,
        camera: str | None = None,
        plan: bool = True,
        force_seeds: bool = False,
    ) -> None:
        super().__init__()
        self._photos = photos
        self._seed_ids = seed_ids
        self._camera = camera
        # plan=False : calibrer + détecter le régime seulement (Calibrate WB,
        # informatif et rapide — pas de décodage as-shot des non-seeds).
        # plan=True : planifier les corrections (Apply WB, autonome).
        self._plan = plan
        # force_seeds : réécrire aussi les seeds (cible = toute la sélection).
        self._force_seeds = force_seeds

    def run(self) -> None:
        try:
            seed_list, others = seeds.collect_seeds(self._photos, self._seed_ids)
            if len(seed_list) < 1:
                self.failed.emit(
                    "Aucun seed trouvé. Corrigez la WB d'au moins 3-5 photos "
                    "(WhiteBalance = Custom) ou sélectionnez-les comme références."
                )
                return
            # Pente physique du boîtier (depuis l'EXIF de la 1re photo si non fourni).
            camera = self._camera or self._photos[0].exif.camera
            slope = wb_model.slope_for_camera(camera)

            cal = wb_model.calibrate(seed_list, slope)
            rep = regime.detect(cal)
            # Régime artistique : as-shot sans pouvoir → Temperature fixe (médiane seeds).
            use_model = rep.regime is not Regime.ARTISTIC
            if self._plan:
                targets = self._photos if self._force_seeds else others
                # WB seule : l'exposition a ses propres boutons (Calibrate/Apply Expo).
                adjustments = seeds.plan_adjustments(
                    targets, cal, apply_exposure=False, use_model=use_model
                )
            else:
                adjustments = []
            self.finished_result.emit(
                CalibrationResult(
                    calibration=cal,
                    regime=rep,
                    adjustments=adjustments,
                    n_seeds=len(seed_list),
                    n_planned=len(adjustments),
                    used_model=use_model,
                )
            )
        except Exception as exc:  # garde-fou
            self.failed.emit(str(exc))
