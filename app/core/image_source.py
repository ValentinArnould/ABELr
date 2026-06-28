"""Sélection de la source pixel pour l'analyse d'une photo.

Politique : **Smart Preview si elle existe, sinon le RAW d'origine.**
Le Smart Preview (DNG JPEG XL 16-bit linéaire) évite de décoder le RAW complet
(~10x plus rapide) ; à défaut on retombe sur le fichier RAW via rawpy.

Sortie uniforme : RGB uint8 référencé écran (sRGB). Le Smart Preview étant stocké
en linéaire, on lui applique la courbe sRGB pour qu'il soit comparable au rendu
rawpy (gamma sRGB) — l'analyse exposition/WB voit la même échelle quelle que soit
la source.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import raw
from .previews import PreviewIndex, decode_smart_preview


@dataclass
class LoadedImage:
    """Image prête pour l'analyse + provenance."""

    rgb: np.ndarray   # HxWx3 uint8, sRGB
    source: str       # "smart_preview" | "raw"
    width: int
    height: int


def _linear_to_srgb_u8(lin: np.ndarray) -> np.ndarray:
    """Convertit du linéaire 0-1 (float) en sRGB 0-255 uint8."""
    lin = np.clip(lin, 0.0, 1.0)
    a = 0.055
    srgb = np.where(lin <= 0.0031308, 12.92 * lin, (1 + a) * np.power(lin, 1 / 2.4) - a)
    return np.clip(srgb * 255.0, 0, 255).astype(np.uint8)


def load_for_analysis(
    photo_id: str,
    raw_path: str | None,
    index: PreviewIndex | None,
    half_size: bool = True,
) -> LoadedImage:
    """Charge une photo pour l'analyse selon la politique smart-preview-puis-RAW.

    - `photo_id` : id_global renvoyé par le plugin (clé de résolution preview).
    - `raw_path` : chemin du RAW (fallback) — fourni par le plugin.
    - `index`    : PreviewIndex ouvert sur le catalogue, ou None (force le RAW).

    Lève FileNotFoundError si ni Smart Preview ni RAW exploitable.
    """
    # 1. Smart Preview disponible ? → on l'utilise.
    if index is not None:
        sp = index.smart_path(photo_id)
        if sp is not None:
            lin = decode_smart_preview(sp, normalize=True)  # float 0-1 linéaire
            rgb = _linear_to_srgb_u8(lin)
            h, w = rgb.shape[:2]
            return LoadedImage(rgb=rgb, source="smart_preview", width=w, height=h)

    # 2. Sinon : décodage du RAW d'origine.
    if not raw_path:
        raise FileNotFoundError(
            f"Aucune Smart Preview pour {photo_id} et aucun chemin RAW fourni."
        )
    rgb = raw.load_rgb(raw_path, half_size=half_size)  # uint8 sRGB
    h, w = rgb.shape[:2]
    return LoadedImage(rgb=rgb, source="raw", width=w, height=h)


def has_smart_preview(photo_id: str, index: PreviewIndex | None) -> bool:
    """True si un Smart Preview existe sur disque pour cette photo."""
    return index is not None and index.smart_path(photo_id) is not None
