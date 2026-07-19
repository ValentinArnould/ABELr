"""Exposure / white balance analysis (numpy).

Two input sources:
- **Linear ProPhoto RAW**: `exposure_stats` / `gray_world_wb` — physical source,
  float32 produced by `image_source.load_for_analysis`. Independent of the applied style.
- **Rendered JPEG preview**: `analyze_preview_jpeg` — Lr render (profile + presets baked in),
  sRGB display-referred u8. Encodes the actual visual result. Better correlation with
  the exposure chosen by the user (r=0.937 on n=10 ground truth, vs 0.914 for RAW).
  WB masked on mid-tones (validated on exposed previews — invalid at exposure ≈ 0).

Consumed by `gui.autocorrect_worker` (`ev100`, `ExposureStats`) and by GPU parity
(`core.gpu_raw` reuses the clipping thresholds). Correction computation lives in
`core.seed_match` / `core.wb_model` / `core.autocorrect`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from . import color

# Clipping thresholds in **linear** (0-1). Highlights: a channel nearly saturated.
# Shadows: near-zero luminance. Adjustable depending on the target render.
_HIGHLIGHT_CLIP = 0.99
_SHADOW_CLIP = 0.0008


@dataclass
class ExposureStats:
    """Exposure metrics of an image (**linear 0-1** scale)."""

    mean_luma: float           # mean Y luminance (linear)
    median_luma: float         # median Y luminance (linear)
    clipped_highlights: float  # fraction of pixels with a channel ≥ 0.99
    clipped_shadows: float     # fraction of pixels with luminance ≤ 0.0008


def exposure_stats(rgb: np.ndarray) -> ExposureStats:
    """Exposure metrics of a linear ProPhoto RGB.

    Luminance via XYZ's Y (exact, gamut-independent). Highlight clipping is
    detected per channel (a single saturated channel is enough), shadows on Y.
    """
    luma = color.luminance(rgb)
    total = luma.size
    return ExposureStats(
        mean_luma=float(luma.mean()),
        median_luma=float(np.median(luma)),
        clipped_highlights=float((rgb >= _HIGHLIGHT_CLIP).any(axis=-1).sum() / total),
        clipped_shadows=float((luma <= _SHADOW_CLIP).sum() / total),
    )


def parse_shutter_seconds(shutter: str | float | None) -> float | None:
    """Converts an EXIF shutter speed into seconds.

    Accepts `"1/200"`, `"0.5"`, `"1\""` (sometimes formatted by the Lr SDK) or a float.
    Returns None if not interpretable.
    """
    if shutter is None:
        return None
    if isinstance(shutter, (int, float)):
        return float(shutter) if shutter > 0 else None
    # French-localized Lr formats slow shutter speeds with a comma ("0,4 s") —
    # normalize before float() (Fable 5 review A-03).
    s = str(shutter).strip().rstrip('"s ').strip().replace(",", ".")
    try:
        if "/" in s:
            num, den = s.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else None
        v = float(s)
        return v if v > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def ev100(
    iso: float | int | None,
    aperture: float | None,
    shutter: str | float | None,
) -> float | None:
    """Exposure Value normalized to ISO 100 from the EXIF exposure triangle.

    `EV100 = log2(aperture² / t) - log2(ISO/100)` where `t` = exposure time (s).
    Measures the scene's ambient light **independently** of the pixels — serves as
    scene context (bright sun ≈ 15-16, indoor ≈ 5-8, night < 3) for the k-NN
    matching and to interpret a deliberate underexposure bias. None if some
    data is missing or invalid.
    """
    t = parse_shutter_seconds(shutter)
    if not iso or not aperture or aperture <= 0 or t is None or t <= 0 or iso <= 0:
        return None
    import math

    return math.log2(aperture * aperture / t) - math.log2(iso / 100.0)


def gray_world_wb(rgb: np.ndarray) -> tuple[float, float]:
    """Gray-world white balance estimate, on **linear** RGB.

    Gray-world hypothesis: on average the scene is neutral. Returns
    (g_over_r_gain, g_over_b_gain) — the residual cast relative to gray, basis for
    suggesting Temperature/Tint. Input must be linear (otherwise gamma bias) and
    in a wide gamut (otherwise clipping bias on saturated colors).
    """
    rgb_f = rgb.astype(np.float32) + 1e-9
    mean_r = rgb_f[..., 0].mean()
    mean_g = rgb_f[..., 1].mean()
    mean_b = rgb_f[..., 2].mean()
    return float(mean_g / mean_r), float(mean_g / mean_b)


# Rec.709 luma weights (sRGB display) — used on JPEG previews.
_REC709 = np.array([0.2126, 0.7152, 0.0722], np.float32)


@dataclass
class PreviewStats:
    """Metrics of a rendered JPEG preview (sRGB display space).

    disp_median / disp_mean : gamma-encoded display luma (0-1, perceptual).
    lin_median / lin_mean   : linearized luma (sRGB → linear, closer to the signal).
    mid_frac                : fraction of mid-tone pixels (lin 0.05-0.6 → reliable WB).
    gw_rg / gw_bg           : gray-world g/r, g/b on masked mid-tones.
                              Invalid if mid_frac < 0.02 (photo too dark → exposure first).
    """

    disp_median: float
    disp_mean: float
    lin_median: float
    lin_mean: float
    mid_frac: float
    gw_rg: float
    gw_bg: float


def _srgb_u8_to_linear(u8: np.ndarray) -> np.ndarray:
    """sRGB uint8 → linear float32 [0, 1] (inverse sRGB transfer curve)."""
    x = u8.astype(np.float32) / 255.0
    a = 0.055
    return np.where(x <= 0.04045, x / 12.92, ((x + a) / (1.0 + a)) ** 2.4)


def analyze_preview_jpeg(path: str | Path) -> PreviewStats:
    """Analyzes a JPEG preview rendered by Lr: exposure + mid-tone-masked WB.

    The preview is sRGB display-referred (profile + presets baked in) → encodes the
    actual visual render, independent of the source RAW. Better correlation with the
    exposure chosen by the user than RAW alone (r=0.937 vs 0.914, n=10).

    **Mandatory order**: always call AFTER a correct exposure correction.
    At exposure ≈ 0 on dark photos (night), mid_frac can be < 2% → gw_rg/bg unreliable
    (gray-world blows up on black pixels). The `mid_frac` field lets you detect this case.

    Accepts two file types:
    - Standard `.jpg` file (requestJpegThumbnail thumbnail) → decoded directly.
    - Extensionless Lr preview file (`Previews.lrdata`) → SOI-seeking for the AgHg
      header (same logic as `previews.decode_rendered_preview`).

    Raises ValueError if the file doesn't exist or fails to decode.
    """
    import cv2

    _JPEG_SOI = b"\xff\xd8\xff"
    p = Path(path)
    if not p.exists():
        raise ValueError(f"Preview not found: {p}")

    # Raw read + JPEG stream detection (handles the AgHg header of .lrfprev
    # files and the extensionless files of Previews.lrdata, since cv2.imread
    # can't identify them by extension).
    data = p.read_bytes()
    start = 0 if data[:3] == _JPEG_SOI else data.find(_JPEG_SOI)
    if start == -1:
        raise ValueError(f"No JPEG stream in {p}")
    arr = np.frombuffer(data, np.uint8, offset=start)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"JPEG decoding failed: {p}")
    rgb = img[:, :, ::-1]  # BGR → RGB uint8

    lin = _srgb_u8_to_linear(rgb)
    disp_luma = (rgb.astype(np.float32) / 255.0) @ _REC709
    lin_luma = lin @ _REC709

    # Mid-tone mask (linear 0.05-0.6) for reliable gray-world.
    mid = (lin_luma > 0.05) & (lin_luma < 0.6)
    mid_frac = float(mid.mean())
    if mid_frac >= 0.02:
        sub = lin[mid]
    else:
        # Fallback: brightest pixels (at least something to measure).
        thresh = float(np.percentile(lin_luma, 80))
        sub = lin[lin_luma > thresh]

    r = float(sub[:, 0].mean())
    g = float(sub[:, 1].mean())
    b = float(sub[:, 2].mean())

    return PreviewStats(
        disp_median=float(np.median(disp_luma)),
        disp_mean=float(disp_luma.mean()),
        lin_median=float(np.median(lin_luma)),
        lin_mean=float(lin_luma.mean()),
        mid_frac=mid_frac,
        gw_rg=g / (r + 1e-9),
        gw_bg=g / (b + 1e-9),
    )
