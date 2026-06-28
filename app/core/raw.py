"""Décodage RAW Sony ARW via rawpy (LibRaw).

Stub initial : ouverture + rendu d'un aperçu numpy. Analyse fine déléguée à analysis.py.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rawpy


def load_rgb(path: str | Path, half_size: bool = True) -> np.ndarray:
    """Décode un fichier RAW (.ARW) en image RGB uint8.

    half_size=True accélère fortement le décodage (suffisant pour l'analyse batch).
    Retourne un array HxWx3.
    """
    with rawpy.imread(str(path)) as raw:
        rgb = raw.postprocess(
            half_size=half_size,
            no_auto_bright=True,
            use_camera_wb=True,
            output_bps=8,
        )
    return rgb


def load_thumbnail(path: str | Path) -> np.ndarray | None:
    """Extrait la miniature JPEG embarquée si disponible (rapide, pour aperçu GUI)."""
    try:
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
    except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
        return None
    if thumb.format == rawpy.ThumbFormat.JPEG:
        import cv2

        data = np.frombuffer(thumb.data, dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)[:, :, ::-1]  # BGR->RGB
    if thumb.format == rawpy.ThumbFormat.BITMAP:
        return thumb.data
    return None
