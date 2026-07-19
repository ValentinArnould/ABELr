"""Masque de « zone nette » — restreint les mesures d'histogramme au sujet net.

Un Laplacien (filtre passe-haut) mesure la netteté locale ; les zones de flou
(bokeh, mouvement, hors-profondeur de champ) ont une magnitude proche de zéro.
On garde le **top `SHARP_TOP_FRACTION`** des pixels les plus nets — c'est sur
cette zone que `render_metrics`/`render_metrics_gpu` calculent tone/neutral/
bandes, pour que l'histogramme reflète le sujet plutôt qu'un arrière-plan flou.

Deux implémentations identiques (même formule, même seuil) :
- `sharp_mask` : numpy, utilisée par les scripts `tools/` (CPU).
- `sharp_mask_gpu` : torch CUDA, utilisée par le chemin live GPU-strict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import torch

SHARP_TOP_FRACTION = 0.25  # top 25% des pixels les plus nets retenus


def _laplacian_magnitude(luma: np.ndarray) -> np.ndarray:
    """|Laplacien| (4*centre - voisins N/S/E/O) sur une carte de luminance HxW."""
    p = np.pad(luma, 1, mode="edge")
    lap = (
        4.0 * p[1:-1, 1:-1]
        - p[:-2, 1:-1]
        - p[2:, 1:-1]
        - p[1:-1, :-2]
        - p[1:-1, 2:]
    )
    return np.abs(lap)


def sharp_mask(luma: np.ndarray, top_fraction: float = SHARP_TOP_FRACTION) -> np.ndarray:
    """Masque bool HxW : True = pixel parmi les `top_fraction` les plus nets.

    `luma` : carte 2D (L* CIELAB pour un rendu sRGB, ou Y linéaire pour un RAW).
    Si l'image est uniforme (magnitude nulle partout), tout est retenu (pas de
    zone nette identifiable → ne pas restreindre).
    """
    mag = _laplacian_magnitude(luma.astype(np.float32))
    if not np.any(mag > 0):
        return np.ones(luma.shape, dtype=bool)
    threshold = np.quantile(mag, 1.0 - top_fraction)
    return mag >= threshold


def sharp_mask_gpu(luma: torch.Tensor, top_fraction: float = SHARP_TOP_FRACTION) -> torch.Tensor:
    """Équivalent CUDA de `sharp_mask`. `luma` : tenseur 2D (H, W) float sur GPU."""
    import torch

    p = torch.nn.functional.pad(luma.float()[None, None], (1, 1, 1, 1), mode="replicate")[0, 0]
    lap = 4.0 * p[1:-1, 1:-1] - p[:-2, 1:-1] - p[2:, 1:-1] - p[1:-1, :-2] - p[1:-1, 2:]
    mag = lap.abs()
    if not torch.any(mag > 0):
        return torch.ones_like(luma, dtype=torch.bool)
    flat = mag.reshape(-1)
    # torch.quantile borne le nombre d'éléments (~16M) — sous-échantillonne au-delà
    # (même pattern que render_metrics_gpu._q ; un grand rendu/RAW dépasse vite ce seuil).
    if flat.numel() > 8_000_000:
        flat = flat[:: (flat.numel() // 8_000_000 + 1)]
    threshold = torch.quantile(flat, 1.0 - top_fraction)
    return mag >= threshold
