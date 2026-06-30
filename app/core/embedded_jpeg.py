"""JPEG embarqué boîtier — prior d'exposition (et look initial).

Chaque ARW contient le JPEG rendu **par le boîtier** : c'est l'exposition jugée
bonne à la prise de vue + le premier look (Creative Look Sony). sRGB display-referred.
On s'en sert comme **repli de cible d'exposition** quand les seeds manquent (décision
utilisateur : seeds d'abord, JPEG boîtier ensuite).

Extraction via `raw.load_thumbnail` (LibRaw `extract_thumb`) ; mesure de clarté via
`render_metrics.tone_stats` (CIE L*, même métrique que le rendu LR → comparable).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import raw, render_metrics
from .render_metrics import ToneStats


def load_embedded_rgb(path: str | Path) -> np.ndarray | None:
    """RGB uint8 sRGB du JPEG embarqué boîtier, ou None si absent/illisible."""
    try:
        thumb = raw.load_thumbnail(path)
    except Exception:
        return None
    if thumb is None:
        return None
    arr = np.asarray(thumb)
    if arr.ndim != 3 or arr.shape[-1] != 3:
        return None
    return arr.astype(np.uint8, copy=False)


def embedded_tone(path: str | Path) -> ToneStats | None:
    """Clarté perçue (CIE L*) du JPEG embarqué boîtier, ou None.

    Même `tone_stats` que sur le rendu LR → la médiane L* est directement comparable
    à la cible d'exposition. Sert de prior quand peu/pas de seeds.
    """
    rgb = load_embedded_rgb(path)
    if rgb is None:
        return None
    return render_metrics.tone_stats(rgb)


def embedded_target_l(path: str | Path) -> float | None:
    """Médiane L* du JPEG boîtier (cible d'exposition de repli), ou None."""
    ts = embedded_tone(path)
    return ts.median_l if ts is not None else None
