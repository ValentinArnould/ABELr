"""Calcul et formatage des corrections develop finales (clés PascalCase SDK Lr).

Stub initial : conversion métriques d'analyse -> dict develop prêt à envoyer au plugin.
"""

from __future__ import annotations

import math

from .analysis import ExposureStats


def exposure_correction(stats: ExposureStats, target_luma: float = 118.0) -> float:
    """Suggère une valeur `Exposure` (EV) pour viser une luminance cible.

    Approximation : Exposure ≈ log2(target / mean). Bornée à ±5 EV (plage SDK).
    """
    mean = max(stats.mean_luma, 1.0)
    ev = math.log2(target_luma / mean)
    return round(max(-5.0, min(5.0, ev)), 2)


def build_develop(exposure: float | None = None, **params) -> dict:
    """Construit un dict develop (PascalCase) en omettant les valeurs None."""
    develop: dict[str, float] = {}
    if exposure is not None:
        develop["Exposure"] = exposure
    for key, value in params.items():
        if value is not None:
            develop[key] = value
    return develop
