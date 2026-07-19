"""'Sharp zone' mask — restricts histogram measurements to the in-focus subject.

A Laplacian (high-pass filter) measures local sharpness; blurred areas (bokeh,
motion, out of depth-of-field) have a magnitude close to zero. We keep the
**top `SHARP_TOP_FRACTION`** sharpest pixels — `render_metrics`/`render_metrics_gpu`
compute tone/neutral/bands over this zone, so the histogram reflects the subject
rather than a blurred background.

Two identical implementations (same formula, same threshold):
- `sharp_mask`: numpy, used by the `tools/` scripts (CPU).
- `sharp_mask_gpu`: torch CUDA, used by the live GPU-strict path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import torch

SHARP_TOP_FRACTION = 0.25  # top 25% sharpest pixels retained


def _laplacian_magnitude(luma: np.ndarray) -> np.ndarray:
    """|Laplacian| (4*center - N/S/E/W neighbors) over an HxW luminance map."""
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
    """Bool mask HxW: True = pixel among the `top_fraction` sharpest.

    `luma`: 2D map (CIELAB L* for an sRGB render, or linear Y for a RAW).
    If the image is uniform (magnitude zero everywhere), everything is kept (no
    identifiable sharp zone → don't restrict).
    """
    mag = _laplacian_magnitude(luma.astype(np.float32))
    if not np.any(mag > 0):
        return np.ones(luma.shape, dtype=bool)
    threshold = np.quantile(mag, 1.0 - top_fraction)
    return mag >= threshold


def sharp_mask_gpu(luma: torch.Tensor, top_fraction: float = SHARP_TOP_FRACTION) -> torch.Tensor:
    """CUDA equivalent of `sharp_mask`. `luma`: 2D tensor (H, W) float on GPU."""
    import torch

    p = torch.nn.functional.pad(luma.float()[None, None], (1, 1, 1, 1), mode="replicate")[0, 0]
    lap = 4.0 * p[1:-1, 1:-1] - p[:-2, 1:-1] - p[2:, 1:-1] - p[1:-1, :-2] - p[1:-1, 2:]
    mag = lap.abs()
    if not torch.any(mag > 0):
        return torch.ones_like(luma, dtype=torch.bool)
    flat = mag.reshape(-1)
    # torch.quantile caps the number of elements (~16M) — subsample beyond that
    # (same pattern as render_metrics_gpu._q; a large render/RAW quickly exceeds this).
    if flat.numel() > 8_000_000:
        flat = flat[:: (flat.numel() // 8_000_000 + 1)]
    threshold = torch.quantile(flat, 1.0 - top_fraction)
    return mag >= threshold
