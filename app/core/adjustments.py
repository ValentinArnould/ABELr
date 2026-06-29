"""Calcul et formatage des corrections develop finales (clés PascalCase SDK Lr).

Convertit des métriques d'analyse en dict `develop` prêt à envoyer au plugin
(job apply_adjustments). Première version : exposition seule. Pas encore câblé au
GUI — la chaîne s'arrête aujourd'hui à l'affichage de l'analyse.
"""

from __future__ import annotations

import math

from .analysis import ExposureStats


def exposure_correction(stats: ExposureStats, target_luma: float = 0.18) -> float:
    """Suggère une valeur `Exposure` (EV) pour viser une luminance cible.

    Échelle **linéaire** (cf. `analysis.ExposureStats`) : `target_luma` = luminance
    Y linéaire visée, défaut 0.18 (gris moyen 18 %). Exposure ≈ log2(target / mean),
    borné à ±5 EV (plage SDK).

    ⚠️ Heuristique brute : pousser la moyenne vers 18 % est faux pour les scènes
    low-key/high-key (une soirée sombre *doit* rester sombre). À remplacer par une
    cible adaptative / un équilibrage relatif sur la série (`prediction.py`).
    """
    mean = max(stats.mean_luma, 1e-6)
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
