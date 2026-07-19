"""Composition of the render-space analysis — single entry point.

`analyze_rendered` measures tone (exposure), neutral (WB cast) and bands (HSL) in
**a single shared CIELAB conversion** (3x less computation than calling the three
separately). This is the API the GUI worker calls on each rendered RGB decoded by
`core.measure`, before calling the `exposure` / `wb_model.refine_temp_tint` / `hsl`
planners.

The rest of the orchestration (submitting `get_thumbnails`/`render_probe` jobs,
building targets from the seeds) lives in the worker, since it depends on the
job queue and the plugin. This module stays pure and testable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import render_metrics, sharpness
from .render_metrics import BandStats, NeutralStats, ToneStats


@dataclass
class RenderAnalysis:
    """Complete measurements of a render (exposure + WB + HSL), one CIELAB pass."""

    tone: ToneStats              # L* lightness → exposure
    neutral: NeutralStats        # a*/b* cast on neutrals → WB refinement
    bands: list[BandStats]       # per-HSL-band stats → HSL calibration


@dataclass
class RenderAnalysisDual:
    """Pair of measurements of a render: **global** (full frame) + **sharp** (sharp zone).

    The global↔sharp delta reveals backlighting (dark sharp subject / bright background)
    and background≠subject cast; `mask_sharp_frac` diagnoses the mask's reliability
    (≈1 = image blurry everywhere → sharp ≈ global, no usable sharp zone).
    """

    sharp: RenderAnalysis
    glob: RenderAnalysis
    mask_sharp_frac: float


def analyze_rendered(rgb_u8: np.ndarray) -> RenderAnalysis:
    """Analyzes a rendered sRGB uint8 RGB in a single shared Lab conversion.

    Restricts tone/neutral/bands to the **sharp zone** (sharpest top 25%,
    `sharpness.sharp_mask` on L*) — excludes bokeh/background blur.
    """
    lab = render_metrics.srgb_u8_to_lab(rgb_u8)
    mask = sharpness.sharp_mask(lab[..., 0])
    return RenderAnalysis(
        tone=render_metrics.tone_stats(rgb_u8, lab, mask=mask),
        neutral=render_metrics.neutral_stats(lab, mask=mask),
        bands=render_metrics.band_stats(rgb_u8, lab, mask=mask),
    )


def analyze_rendered_dual(rgb_u8: np.ndarray) -> RenderAnalysisDual:
    """Like `analyze_rendered` but returns **global + sharp zone** in one Lab pass.

    The CIELAB conversion and the sharpness map are computed once and shared
    between the two scopes (global = `mask=None`, sharp = sharp mask).
    """
    lab = render_metrics.srgb_u8_to_lab(rgb_u8)
    mask = sharpness.sharp_mask(lab[..., 0])
    glob = RenderAnalysis(
        tone=render_metrics.tone_stats(rgb_u8, lab, mask=None),
        neutral=render_metrics.neutral_stats(lab, mask=None),
        bands=render_metrics.band_stats(rgb_u8, lab, mask=None),
    )
    sharp = RenderAnalysis(
        tone=render_metrics.tone_stats(rgb_u8, lab, mask=mask),
        neutral=render_metrics.neutral_stats(lab, mask=mask),
        bands=render_metrics.band_stats(rgb_u8, lab, mask=mask),
    )
    return RenderAnalysisDual(sharp=sharp, glob=glob, mask_sharp_frac=float(mask.mean()))


def band_map(analysis: RenderAnalysis) -> dict[str, BandStats]:
    """Access by band name (Red, Orange, …)."""
    return {b.name: b for b in analysis.bands}
