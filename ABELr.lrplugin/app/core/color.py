"""Analysis color spaces — constants and conversions.

Decisions derived from calibration on a real catalog (see project_overview /
CLAUDE.md "Image pipeline"):

- **Working space = linear ProPhoto RGB.** The analysis runs in a wide gamut
  because sRGB **clips** saturated colors (artificial lighting) and **biases**
  the white balance's per-channel statistics (up to x2 on gray-world ratios
  vs ProPhoto). Exposure itself would be correct in sRGB, but everything is
  unified in ProPhoto.
- **Luminance via XYZ's Y.** The Y row of the ProPhoto(D50)->XYZ matrix gives
  true luminance, gamut-independent (~= Rec.709 luma within 0.05 stop in sRGB).
- **sRGB reserved for display** (GUI thumbnails), never for analysis.
"""

from __future__ import annotations

import numpy as np

# rawpy name of the analysis's decoding color space (see raw.load_linear).
ANALYSIS_COLOR_SPACE = "ProPhoto"

# ProPhoto (ROMM) D50 -> XYZ(D50). The 2nd row = luminance Y.
_PP_TO_XYZ_D50 = np.array(
    [
        [0.7976749, 0.1351917, 0.0313534],
        [0.2880402, 0.7118741, 0.0000857],
        [0.0000000, 0.0000000, 0.8252100],
    ],
    np.float32,
)

# Luminance weights in the linear ProPhoto working space: Y = rgb . this vector.
PROPHOTO_TO_Y = _PP_TO_XYZ_D50[1].copy()  # (0.2880402, 0.7118741, 0.0000857)

# Bradford D50 -> D65 chromatic adaptation.
_BRADFORD_D50_D65 = np.array(
    [
        [0.9555766, -0.0230393, 0.0631636],
        [-0.0282895, 1.0099416, 0.0210077],
        [0.0122982, -0.0204830, 1.3299098],
    ],
    np.float32,
)

# XYZ(D65) -> linear sRGB.
_XYZ_D65_TO_SRGB = np.array(
    [
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ],
    np.float32,
)

# Composed: linear ProPhoto(D50) -> linear sRGB(D65) (for display).
PROPHOTO_TO_SRGB = (_XYZ_D65_TO_SRGB @ _BRADFORD_D50_D65 @ _PP_TO_XYZ_D50).astype(np.float32)


def luminance(rgb_prophoto: np.ndarray) -> np.ndarray:
    """Luminance Y (linear) of a linear ProPhoto RGB. HxWx3 -> HxW."""
    return rgb_prophoto.astype(np.float32) @ PROPHOTO_TO_Y


def linear_to_srgb(lin: np.ndarray) -> np.ndarray:
    """Applies the sRGB transfer curve to linear 0-1 data (float -> float 0-1)."""
    lin = np.clip(lin, 0.0, 1.0)
    a = 0.055
    return np.where(lin <= 0.0031308, 12.92 * lin, (1 + a) * np.power(lin, 1 / 2.4) - a)


def prophoto_linear_to_srgb_u8(rgb_prophoto: np.ndarray) -> np.ndarray:
    """Linear ProPhoto -> display-referred sRGB uint8 (GUI display).

    Primaries conversion (ProPhoto->sRGB) then sRGB curve. Colors outside the
    sRGB gamut are clipped — acceptable for a preview, never for analysis.
    """
    srgb_lin = rgb_prophoto.astype(np.float32) @ PROPHOTO_TO_SRGB.T
    srgb = linear_to_srgb(srgb_lin)
    return np.clip(srgb * 255.0, 0, 255).astype(np.uint8)
