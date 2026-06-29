"""Modèle de prédiction des ajustements sur série 500-1000 photos.

Pas encore implémenté. Cible : lisser / interpoler les corrections le long d'une
série temporelle (scipy / scikit-learn) pour harmoniser exposition et WB sur tout
un reportage. `smooth_series` est un placeholder en attendant le vrai modèle.
"""

from __future__ import annotations

from typing import Sequence


def smooth_series(values: Sequence[float], window: int = 5) -> list[float]:
    """Lissage moyenne glissante (placeholder avant modèle complet)."""
    if window <= 1 or len(values) < window:
        return list(values)
    out: list[float] = []
    half = window // 2
    n = len(values)
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        chunk = values[lo:hi]
        out.append(sum(chunk) / len(chunk))
    return out
