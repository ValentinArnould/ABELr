"""Analyse exposition / balance des blancs / couleur (numpy + OpenCV).

Stub initial : métriques de base par image. Les algos d'équilibrage batch
(prediction.py, adjustments.py) consomment ces métriques.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class ExposureStats:
    """Métriques d'exposition d'une image RGB uint8."""

    mean_luma: float          # luminance moyenne 0-255
    median_luma: float
    clipped_highlights: float  # fraction de pixels proches du blanc
    clipped_shadows: float     # fraction de pixels proches du noir


def _luma(rgb: np.ndarray) -> np.ndarray:
    """Luminance perceptuelle (Rec.709)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def exposure_stats(rgb: np.ndarray) -> ExposureStats:
    """Calcule les métriques d'exposition d'une image RGB."""
    luma = _luma(rgb.astype(np.float32))
    total = luma.size
    return ExposureStats(
        mean_luma=float(luma.mean()),
        median_luma=float(np.median(luma)),
        clipped_highlights=float((luma > 250).sum() / total),
        clipped_shadows=float((luma < 5).sum() / total),
    )


def gray_world_wb(rgb: np.ndarray) -> tuple[float, float]:
    """Estimation balance des blancs gray-world.

    Retourne (gain_r_sur_g, gain_b_sur_g) — base pour suggérer Temperature/Tint.
    """
    rgb_f = rgb.astype(np.float32) + 1e-6
    mean_r = rgb_f[..., 0].mean()
    mean_g = rgb_f[..., 1].mean()
    mean_b = rgb_f[..., 2].mean()
    return float(mean_g / mean_r), float(mean_g / mean_b)
