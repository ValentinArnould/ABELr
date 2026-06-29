"""Analyse exposition / balance des blancs (numpy).

Entrée attendue : **RGB float32 ProPhoto linéaire** dans [0, 1], tel que produit
par `image_source.load_for_analysis` (décodage RAW via `raw.load_linear`). Travail
en linéaire = WB et clipping mesurés correctement ; gamut large = pas de biais sur
les ratios de canaux (cf. `core/color`).

Métriques consommées par `gui.analysis_worker` (affichage) et, à terme, par les
algos d'équilibrage batch (`prediction.py`, `adjustments.py`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import color

# Seuils de clipping en **linéaire** (0-1). Hautes lumières : un canal quasi saturé.
# Ombres : luminance quasi nulle. Réglables selon le rendu visé.
_HIGHLIGHT_CLIP = 0.99
_SHADOW_CLIP = 0.0008


@dataclass
class ExposureStats:
    """Métriques d'exposition d'une image (échelle **linéaire 0-1**)."""

    mean_luma: float           # luminance Y moyenne (linéaire)
    median_luma: float         # luminance Y médiane (linéaire)
    clipped_highlights: float  # fraction de pixels à canal ≥ 0.99
    clipped_shadows: float     # fraction de pixels à luminance ≤ 0.0008


def exposure_stats(rgb: np.ndarray) -> ExposureStats:
    """Métriques d'exposition d'un RGB ProPhoto linéaire.

    Luminance via Y de XYZ (exacte, indépendante du gamut). Le clipping hautes
    lumières est détecté par canal (un seul canal saturé suffit), les ombres sur Y.
    """
    luma = color.luminance(rgb)
    total = luma.size
    return ExposureStats(
        mean_luma=float(luma.mean()),
        median_luma=float(np.median(luma)),
        clipped_highlights=float((rgb >= _HIGHLIGHT_CLIP).any(axis=-1).sum() / total),
        clipped_shadows=float((luma <= _SHADOW_CLIP).sum() / total),
    )


def gray_world_wb(rgb: np.ndarray) -> tuple[float, float]:
    """Estimation balance des blancs gray-world, sur RGB **linéaire**.

    Hypothèse gray-world : en moyenne la scène est neutre. Retourne
    (gain_g_sur_r, gain_g_sur_b) — le cast résiduel par rapport au gris, base pour
    suggérer Temperature/Tint. L'entrée doit être linéaire (sinon biais gamma) et
    en gamut large (sinon biais d'écrêtage des couleurs saturées).
    """
    rgb_f = rgb.astype(np.float32) + 1e-9
    mean_r = rgb_f[..., 0].mean()
    mean_g = rgb_f[..., 1].mean()
    mean_b = rgb_f[..., 2].mean()
    return float(mean_g / mean_r), float(mean_g / mean_b)
