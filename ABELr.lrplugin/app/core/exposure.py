"""Exposure balancing — render space, target resolved by the caller (embedded or k-NN seeds).

Perceived exposure lives in the **render** (DCP profile + curve + sliders), not in
scene-linear space. We measure the current render's **CIE L\\*** lightness
(`render_metrics.tone_stats`, sharp zone) and compare it to a **target lightness**
already resolved by the caller (`core.autocorrect`):

- **embedded** mode: target = L* of the photo's own in-camera JPEG (sharp zone).
- **seeds** mode: target = L* of the rendered (already edited) preview of the
  closest seed(s) by RAW analysis (`core.seed_match.match_target`).

The gap is translated into ΔExposure2012 via the **calibrated response** `∂L*/∂EV`
(`core.response`), bounded by a max step and a **headroom** safeguard (RAW
clipping, doesn't push further into clipping that's already present). The new
`Exposure2012` accumulates this delta on top of the **current** develop value —
supplied by the caller, which must have measured it fresh (the `current_l`
render must reflect this current value, otherwise the recalculated delta is
meaningless).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .response import ExposureResponse
from ..server.models import PhotoAdjustment

# RAW clipping thresholds (fractions) beyond which the correction is throttled.
_HI_LIMIT = 0.02
_LO_LIMIT = 0.02
# Maximum exposure step per photo (safety net against an outlier measurement).
_MAX_STEP_EV = 2.0


@dataclass
class ExposureSample:
    """Measurement of a target photo + its desired lightness (already resolved by the caller).

    `current_l`    : median L* of the **current render** (sharp zone), must reflect
                      `current_exposure` (fresh measurement — caller's responsibility).
    `desired_l`     : targeted L* lightness (in-camera JPEG or matched seeds). `None` =
                      no usable target → photo left unchanged.
    """

    photo_id: str
    current_l: float
    current_exposure: float
    desired_l: float | None
    clipped_hi: float = 0.0   # fraction of clipped highlights (RAW) — headroom
    clipped_lo: float = 0.0   # fraction of blocked shadows (RAW) — headroom


def _headroom_factor(clip: float, limit: float) -> float:
    """Factor [0, 1]: 1 below the threshold, decays to 0 at 2× the threshold."""
    if clip <= limit:
        return 1.0
    return max(0.0, 1.0 - (clip - limit) / limit)


def plan_from_render(
    samples: list[ExposureSample],
    resp: ExposureResponse | None = None,
    max_step_ev: float = _MAX_STEP_EV,
    hi_limit: float = _HI_LIMIT,
    lo_limit: float = _LO_LIMIT,
) -> list[PhotoAdjustment]:
    """Plans Exposure2012 to bring each photo to its `desired_l` lightness.

    ΔEV = `resp.solve_dev(current_l → desired_l)`, bounded to ±`max_step_ev`, then
    attenuated by the RAW headroom. The new Exposure2012 accumulates the delta on
    top of `current_exposure`. Photos with no `desired_l`: skipped (nothing to apply).
    """
    resp = resp or ExposureResponse()
    out: list[PhotoAdjustment] = []
    for s in samples:
        if s.desired_l is None:
            continue
        dev = resp.solve_dev(s.current_l, s.desired_l)
        dev = float(np.clip(dev, -max_step_ev, max_step_ev))
        if dev > 0:
            dev *= _headroom_factor(s.clipped_hi, hi_limit)
        elif dev < 0:
            dev *= _headroom_factor(s.clipped_lo, lo_limit)
        new_ev = round(s.current_exposure + dev, 2)
        out.append(PhotoAdjustment(photo_id=s.photo_id, develop={"Exposure2012": new_ev}))
    return out
