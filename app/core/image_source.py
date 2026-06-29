"""Source pixel de l'analyse : décodage RAW en ProPhoto linéaire.

Politique (depuis la calibration sur catalogue réel) : **l'analyse part du RAW
d'origine via rawpy.** La Smart Preview a été écartée du chemin d'analyse : son DNG
stocke du raw caméra-natif (PhotometricInterpretation = LinearRaw, avant WB et
avant matrice couleur), que LibRaw ne sait pas décoder (tuiles JPEG XL) et qu'un
dérawmatiseur fait main ne reproduit pas fidèlement. Détails dans `previews.py`.

Sortie de l'analyse : **RGB float32 ProPhoto linéaire** (gamut large, sans gamma) —
voir `core/color` pour le choix d'espace. Un rendu uint8 sRGB display-referred est
disponible à la demande pour l'affichage (`LoadedImage.display_u8`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import color, raw


@dataclass
class LoadedImage:
    """Image décodée pour l'analyse + provenance."""

    rgb: np.ndarray   # HxWx3 float32, ProPhoto linéaire 0-1
    source: str       # "raw" (seule source d'analyse actuelle)
    colorspace: str   # "prophoto_linear"
    width: int
    height: int

    def display_u8(self) -> np.ndarray:
        """Rendu uint8 sRGB pour l'affichage GUI (couleurs hors gamut écrêtées)."""
        return color.prophoto_linear_to_srgb_u8(self.rgb)


def load_for_analysis(raw_path: str, half_size: bool = True) -> LoadedImage:
    """Décode un RAW pour l'analyse (ProPhoto linéaire).

    `raw_path` : chemin du fichier RAW fourni par le plugin. Lève FileNotFoundError
    si absent / vide.
    """
    if not raw_path:
        raise FileNotFoundError("Chemin RAW manquant pour l'analyse.")
    rgb = raw.load_linear(raw_path, half_size=half_size)  # float32 ProPhoto linéaire
    h, w = rgb.shape[:2]
    return LoadedImage(
        rgb=rgb, source="raw", colorspace="prophoto_linear", width=w, height=h
    )
