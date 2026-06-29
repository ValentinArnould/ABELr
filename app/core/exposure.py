"""Calibration d'exposition par seeds — médiane robuste des Exposure2012.

Sur un event typique, l'exposition voulue par le photographe est quasi-constante
(σ ≈ 0.04 EV sur CGC, cf. `core.wb_model`). On la calibre donc par la **médiane**
des seeds (photos corrigées à la main, `WhiteBalance="Custom"`) et on l'applique
telle quelle aux autres photos de la sélection.

Contrairement à la WB, **aucune dépendance à l'as-shot** : pas de décodage RAW.
On lit `Exposure2012` directement dans les develop settings retournés par le
plugin → calcul instantané, exécutable sur le thread GUI.

Le seed est identifié par `core.seeds.is_seed` (WB Custom), cohérent avec le
calibrage WB.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import seeds as _seeds
from ..server.models import PhotoAdjustment, PhotoResult


@dataclass
class ExposureCalibration:
    """Exposition calibrée sur les seeds d'une sélection."""

    exposure: float    # Exposure2012 à appliquer (médiane des seeds, EV)
    spread_ev: float   # écart-type des seeds (homogénéité / confiance)
    n_seeds: int


def _exposure_of(develop: dict) -> float | None:
    """Exposure2012 (ou alias Exposure) du dict develop, ou None si absent."""
    for k in ("Exposure2012", "Exposure"):
        v = develop.get(k)
        if v is not None:
            return float(v)
    return None


def collect_exposures(
    photos: list[PhotoResult],
    seed_ids: set[str] | None = None,
) -> tuple[list[float], list[PhotoResult]]:
    """Sépare (valeurs EV des seeds, photos à corriger). Pas de décodage RAW.

    Un seed sans Exposure2012 lisible bascule dans « à corriger ». Si `seed_ids`
    est fourni il prime (sélection explicite GUI), sinon heuristique `is_seed`.
    """
    exposures: list[float] = []
    others: list[PhotoResult] = []
    for p in photos:
        dev = p.current_develop or {}
        chosen = (p.photo_id in seed_ids) if seed_ids is not None else _seeds.is_seed(dev)
        ev = _exposure_of(dev)
        if chosen and ev is not None:
            exposures.append(ev)
        else:
            others.append(p)
    return exposures, others


def calibrate(exposures: list[float]) -> ExposureCalibration:
    """Calibre l'exposition depuis les valeurs EV des seeds (médiane robuste)."""
    if not exposures:
        raise ValueError(
            "Aucun seed d'exposition. Corrigez l'exposition d'au moins une photo "
            "(WhiteBalance = Custom) ou sélectionnez-la comme référence."
        )
    arr = np.asarray(exposures, np.float64)
    return ExposureCalibration(
        exposure=float(np.median(arr)),
        spread_ev=float(np.std(arr)) if len(arr) > 1 else 0.0,
        n_seeds=len(arr),
    )


def plan_adjustments(
    photos: list[PhotoResult],
    cal: ExposureCalibration,
) -> list[PhotoAdjustment]:
    """Applique l'exposition calibrée (constante) à chaque photo cible.

    Exposition seule : ne touche ni WB ni autre réglage (boutons WB dédiés).
    """
    ev = round(cal.exposure, 2)
    return [
        PhotoAdjustment(photo_id=p.photo_id, develop={"Exposure2012": ev})
        for p in photos
    ]
