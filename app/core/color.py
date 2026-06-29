"""Espaces couleur de l'analyse — constantes et conversions.

Décisions issues de la calibration sur catalogue réel (cf. project_overview /
CLAUDE.md « Pipeline image ») :

- **Espace de travail = ProPhoto RGB linéaire.** L'analyse tourne en gamut large
  car sRGB **écrête** les couleurs saturées (lumières artificielles) et **biaise**
  les statistiques par canal de la balance des blancs (jusqu'à ×2 sur les ratios
  gray-world vs ProPhoto). L'exposition, elle, serait correcte en sRGB, mais on
  unifie tout en ProPhoto.
- **Luminance via Y de XYZ.** La ligne Y de la matrice ProPhoto(D50)→XYZ donne la
  luminance vraie, indépendante du gamut (≈ luma Rec.709 à 0.05 stop près en sRGB).
- **sRGB réservé à l'affichage** (vignettes GUI), jamais à l'analyse.
"""

from __future__ import annotations

import numpy as np

# Nom rawpy de l'espace de décodage de l'analyse (cf. raw.load_linear).
ANALYSIS_COLOR_SPACE = "ProPhoto"

# ProPhoto (ROMM) D50 → XYZ(D50). La 2e ligne = luminance Y.
_PP_TO_XYZ_D50 = np.array(
    [
        [0.7976749, 0.1351917, 0.0313534],
        [0.2880402, 0.7118741, 0.0000857],
        [0.0000000, 0.0000000, 0.8252100],
    ],
    np.float32,
)

# Poids de luminance dans l'espace de travail ProPhoto linéaire : Y = rgb · ce vecteur.
PROPHOTO_TO_Y = _PP_TO_XYZ_D50[1].copy()  # (0.2880402, 0.7118741, 0.0000857)

# Adaptation chromatique Bradford D50 → D65.
_BRADFORD_D50_D65 = np.array(
    [
        [0.9555766, -0.0230393, 0.0631636],
        [-0.0282895, 1.0099416, 0.0210077],
        [0.0122982, -0.0204830, 1.3299098],
    ],
    np.float32,
)

# XYZ(D65) → sRGB linéaire.
_XYZ_D65_TO_SRGB = np.array(
    [
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ],
    np.float32,
)

# Composée : ProPhoto(D50) linéaire → sRGB(D65) linéaire (pour l'affichage).
PROPHOTO_TO_SRGB = (_XYZ_D65_TO_SRGB @ _BRADFORD_D50_D65 @ _PP_TO_XYZ_D50).astype(np.float32)


def luminance(rgb_prophoto: np.ndarray) -> np.ndarray:
    """Luminance Y (linéaire) d'un RGB ProPhoto linéaire. HxWx3 → HxW."""
    return rgb_prophoto.astype(np.float32) @ PROPHOTO_TO_Y


def linear_to_srgb(lin: np.ndarray) -> np.ndarray:
    """Applique la courbe de transfert sRGB à du linéaire 0-1 (float → float 0-1)."""
    lin = np.clip(lin, 0.0, 1.0)
    a = 0.055
    return np.where(lin <= 0.0031308, 12.92 * lin, (1 + a) * np.power(lin, 1 / 2.4) - a)


def prophoto_linear_to_srgb_u8(rgb_prophoto: np.ndarray) -> np.ndarray:
    """ProPhoto linéaire → sRGB uint8 display-referred (affichage GUI).

    Conversion de primaires (ProPhoto→sRGB) puis courbe sRGB. Les couleurs hors
    gamut sRGB sont écrêtées — acceptable pour un aperçu, jamais pour l'analyse.
    """
    srgb_lin = rgb_prophoto.astype(np.float32) @ PROPHOTO_TO_SRGB.T
    srgb = linear_to_srgb(srgb_lin)
    return np.clip(srgb * 255.0, 0, 255).astype(np.uint8)
