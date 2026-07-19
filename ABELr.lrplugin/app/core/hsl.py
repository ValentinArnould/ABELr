"""Per-band HSL calibration — reduce oversaturation, adjust luminance,
recenter hue (user objective).

Measured on the **rendered output** (`render_metrics.band_stats`, 8 Lr bands); the RAW
can serve as a **guard** (only reduce a band's saturation if the RAW confirms it was
genuinely heavy at capture, not a profile artifact). Measurement-to-slider inversion
via the **calibrated response** per band (`core.response.BandResponse`); without
calibration, a bounded conservative nudge is applied (transparent heuristic).

Sliders emitted (SDK names, absolute values -100...+100): `SaturationAdjustment<Band>`,
`LuminanceAdjustment<Band>`, `HueAdjustment<Band>`. The computed deltas are **added
to the slider's current value** (the measured render already reflects the current sliders).

Warning: **Experimental** — needs ground-truth validation (`tools/validate_hsl.py` script)
before being trusted. Tight guardrails by default (populated bands, capped deltas,
dead zone) so it never over-corrects.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import render_metrics
from .render_metrics import BandStats
from .response import BandResponse, ResponseModel

# --------------------------------------------------------------------------- #
# Guardrails and nominal gains (heuristics, to be replaced by the calibration survey)
# --------------------------------------------------------------------------- #
# Minimum population of a band for it to be corrected.
_MIN_FRAC = render_metrics._BAND_MIN_FRAC
# Dead zones: below this, leave it alone (anti micro-correction).
_DEADBAND_CHROMA = 4.0   # dC* CIELAB
_DEADBAND_L = 3.0        # dL*
_DEADBAND_HUE = 6.0      # degrees
# Slider delta caps (Lr units).
_MAX_SAT = 25
_MAX_LUM = 20
_MAX_HUE = 15
# Dedicated, stricter caps when the target is a raw transplant from the camera
# JPEG (`BandTarget.embedded_raw=True`, `ignore_bias=True` mode): the JPEG has its
# own color science (creative profile) on L*/hue, we don't want to copy it
# wholesale ("correct, don't copy") — only saturation already had a
# reduction-only guard, L*/hue had none before H3.
_MAX_LUM_EMBEDDED_RAW = 10
_MAX_HUE_EMBEDDED_RAW = 8
# Fraction of near-saturated pixels (S>=0.97) that triggers a reduction even
# without a chroma reference ("hard" oversaturation).
_SAT_CLIP_TRIGGER = 0.05

# Nominal gains when the response is NOT calibrated (bounded heuristics):
# +1 unit of SaturationAdjustment ~= +0.6 C*; +1 Luminance ~= +0.4 L*;
# +1 Hue ~= +0.35 degrees (typical Lr orders of magnitude — to be confirmed by survey).
_NOM_DCHROMA_DSAT = 0.6
_NOM_DL_DLUM = 0.4
_NOM_DHUE_DHUE = 0.35


@dataclass
class BandTarget:
    """Reference for a band (median of the seeds / reference image). Optional fields.

    raw_oversat: if False, the RAW does NOT confirm this band's load -> saturation
    reduction is forbidden for it (avoids correcting a profile effect).
    None = no RAW info (does not block).

    embedded_raw: True if `chroma`/`lstar`/`hue` are a raw transplant from the camera
    JPEG (`ignore_bias=True` mode, no bias norm subtracted) -> stricter L*/hue caps
    (`_MAX_LUM_EMBEDDED_RAW`/`_MAX_HUE_EMBEDDED_RAW`) so the creative profile's color
    science is never copied wholesale.
    """

    name: str
    chroma: float | None = None
    lstar: float | None = None
    hue: float | None = None
    raw_oversat: bool | None = None
    embedded_raw: bool = False


def raw_confirms_oversat(raw_band: BandStats | None, min_frac: float = _MIN_FRAC) -> bool | None:
    """Does the RAW (sharp zone) confirm that a band is genuinely heavy at
    capture? Populates `BandTarget.raw_oversat` (anti-over-correction guard: don't
    reduce a saturation that the render shows but the RAW contradicts).

    None: no RAW measurement for this band, or insufficient RAW population
    (`min_frac`) -> no info, does not block (historical behavior).
    True/False otherwise, on the same hard-oversaturation threshold (`_SAT_CLIP_TRIGGER`,
    near-saturated pixels S>=0.97) used on the render — direct sensor evidence,
    not an invented chroma threshold.
    """
    if raw_band is None or raw_band.frac < min_frac:
        return None
    return raw_band.sat_clip_frac >= _SAT_CLIP_TRIGGER


@dataclass
class HslCorrection:
    """HSL slider deltas decided for a band (diagnostic + application)."""

    name: str
    d_saturation: int = 0
    d_luminance: int = 0
    d_hue: int = 0
    reason: str = ""


def _hue_diff(a: float, b: float) -> float:
    """Signed circular difference a-b in (-180, 180]."""
    return (a - b + 180.0) % 360.0 - 180.0


def _clamp(v: float, lo: int, hi: int) -> int:
    return int(round(max(lo, min(hi, v))))


def plan_band(
    stats: BandStats,
    target: BandTarget | None,
    resp: BandResponse,
    *,
    min_frac: float = _MIN_FRAC,
) -> HslCorrection | None:
    """Decides the HSL deltas for a band, or None if nothing to do / band unreliable."""
    if not render_metrics.band_is_reliable(stats, min_frac):
        return None

    reasons: list[str] = []
    d_sat = d_lum = d_hue = 0

    # --- Saturation: REDUCTION ONLY of oversaturation -------------------------
    # User objective: "mainly reduce excessive saturation". We NEVER raise
    # saturation (otherwise we'd copy the oversaturation of a punchy camera
    # JPEG) -> we only act when the render is MORE saturated than the
    # reference (positive excess), and the delta is capped at <= 0.
    target_chroma = target.chroma if target else None
    excess = 0.0
    if target_chroma is not None:
        excess = stats.median_chroma - target_chroma  # >0 = too saturated vs reference
    # Hard oversaturation (S-clipped pixels) -> reduce even without a reference.
    if stats.sat_clip_frac >= _SAT_CLIP_TRIGGER:
        excess = max(excess, _DEADBAND_CHROMA + 1.0)
        reasons.append(f"sat_clip={stats.sat_clip_frac:.2f}")
    # The RAW can forbid a reduction (band not loaded at capture).
    raw_blocks = target is not None and target.raw_oversat is False
    if excess >= _DEADBAND_CHROMA and not raw_blocks:
        gain = resp.dchroma_dsat if abs(resp.dchroma_dsat) > 1e-9 else _NOM_DCHROMA_DSAT
        d_sat = _clamp(-excess / gain, -_MAX_SAT, 0)  # reduction only (<= 0)
        if d_sat:
            reasons.append(f"ΔC*={excess:+.1f}→sat{d_sat:+d}")

    # --- Luminance: move closer to the reference lightness --------------------
    strict = target is not None and target.embedded_raw
    if target and target.lstar is not None:
        dl = target.lstar - stats.median_l
        if abs(dl) >= _DEADBAND_L:
            gain = resp.dl_dlum if abs(resp.dl_dlum) > 1e-9 else _NOM_DL_DLUM
            max_lum = _MAX_LUM_EMBEDDED_RAW if strict else _MAX_LUM
            d_lum = _clamp(dl / gain, -max_lum, max_lum)
            if d_lum:
                reasons.append(f"ΔL*={dl:+.1f}→lum{d_lum:+d}")

    # --- Hue: recenter the drift ----------------------------------------------
    if target and target.hue is not None:
        dh = _hue_diff(target.hue, stats.median_hue)  # amount to add to the hue
        if abs(dh) >= _DEADBAND_HUE:
            gain = resp.dhue_dhue if abs(resp.dhue_dhue) > 1e-9 else _NOM_DHUE_DHUE
            max_hue = _MAX_HUE_EMBEDDED_RAW if strict else _MAX_HUE
            d_hue = _clamp(dh / gain, -max_hue, max_hue)
            if d_hue:
                reasons.append(f"Δhue={dh:+.1f}°→hue{d_hue:+d}")

    if not (d_sat or d_lum or d_hue):
        return None
    return HslCorrection(stats.name, d_sat, d_lum, d_hue, ", ".join(reasons))


def plan_hsl(
    band_stats: list[BandStats],
    targets: dict[str, BandTarget] | None,
    model: ResponseModel | None = None,
    *,
    min_frac: float = _MIN_FRAC,
) -> tuple[dict[str, int], list[HslCorrection]]:
    """Plans the HSL corrections for all bands.

    Returns (develop_delta, corrections) where `develop_delta` is a dict of SDK keys
    (`SaturationAdjustment<Band>`, etc.) -> **delta** to add to the current value.
    The worker sums it with the current values before sending the job.
    """
    targets = targets or {}
    develop: dict[str, int] = {}
    corrections: list[HslCorrection] = []
    for stats in band_stats:
        resp = model.band(stats.name) if model else BandResponse()
        corr = plan_band(stats, targets.get(stats.name), resp, min_frac=min_frac)
        if corr is None:
            continue
        corrections.append(corr)
        if corr.d_saturation:
            develop[f"SaturationAdjustment{stats.name}"] = corr.d_saturation
        if corr.d_luminance:
            develop[f"LuminanceAdjustment{stats.name}"] = corr.d_luminance
        if corr.d_hue:
            develop[f"HueAdjustment{stats.name}"] = corr.d_hue
    return develop, corrections
