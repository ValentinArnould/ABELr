"""Métriques en espace rendu — **portage torch** de `render_metrics` (GPU ou CPU).

Mêmes mesures que `render_metrics` (tone L*, neutres a*/b*, bandes HSL) et **mêmes
dataclasses** (`ToneStats`, `NeutralStats`, `BandStats`), mais tout le calcul tourne
en tenseurs torch plutôt qu'en numpy. Le device suit `gpu.device()` (GPU prioritaire,
repli CPU si aucun CUDA utilisable — cf. `core/gpu.py`). Sur GPU, l'entrée est un
tenseur **uint8 CHW RGB déjà sur device** tel que le rend nvJPEG
(`torchvision.io.decode_jpeg(device='cuda')`) → aucun aller-retour CPU entre le
décodage et la mesure ; sur CPU, même chemin, tenseurs CPU.

Les constantes colorimétriques (sRGB→XYZ→CIELAB, seuils) sont **importées** de
`render_metrics` pour rester l'unique source de vérité ; le portage doit reproduire
la version numpy (vérifié par `tools/validate_gpu_vs_libraw` / tests dédiés).
"""

from __future__ import annotations

import torch

from . import gpu
from . import render_metrics as rm
from .pipeline import RenderAnalysis, RenderAnalysisDual
from .render_metrics import BandStats, NeutralStats, ToneStats

# Résolu une fois : GPU si disponible, sinon CPU (gpu.device() ne lève jamais).
# Les modules amont (gpu_jpeg, gpu_schedule) décodent déjà sur ce même device via
# gpu.device() — cohérent, jamais de tenseur CUDA mélangé à un calcul CPU.
_DEV = gpu.device()


def _const(arr) -> torch.Tensor:
    """Constante numpy → tenseur float32 sur le device courant (GPU ou CPU)."""
    return torch.as_tensor(arr, dtype=torch.float32, device=_DEV)


# Matrices/constantes (mêmes valeurs que render_metrics).
_SRGB_LIN_TO_XYZ = _const(rm._SRGB_LIN_TO_XYZ_D65)        # 3x3
_D65 = _const(rm._D65_WHITE)                              # (3,)
_BAND_CENTERS = _const(rm._BAND_CENTERS)                  # (8,)
_LAB_DELTA3 = float(rm._LAB_DELTA3)
_LAB_SLOPE = float(rm._LAB_SLOPE)
_LAB_OFFSET = float(rm._LAB_OFFSET)
_HIGHLIGHT_U8 = float(rm._HIGHLIGHT_U8)
_SHADOW_L = float(rm._SHADOW_L)
_NEUTRAL_CHROMA = float(rm._NEUTRAL_CHROMA)
_NEUTRAL_L_MIN = float(rm._NEUTRAL_L_MIN)
_NEUTRAL_L_MAX = float(rm._NEUTRAL_L_MAX)
_BAND_MIN_FRAC = float(rm._BAND_MIN_FRAC)
_BAND_NAMES = rm.BAND_NAMES


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _to_hwc_u8(chw_u8: torch.Tensor) -> torch.Tensor:
    """nvJPEG/CPU sort du CHW uint8 ; on travaille en HWC. Force RGB 3 canaux sur le device courant."""
    if chw_u8.device != _DEV:
        chw_u8 = chw_u8.to(_DEV)
    if chw_u8.dim() == 3 and chw_u8.shape[0] in (1, 3):
        hwc = chw_u8.permute(1, 2, 0)
    else:
        hwc = chw_u8
    if hwc.shape[-1] == 1:
        hwc = hwc.expand(-1, -1, 3)
    return hwc.contiguous()


def _q(x: torch.Tensor, q: float) -> float:
    """Quantile (interpolation linéaire, comme numpy) d'un tenseur 1D, en float.

    `torch.quantile` borne le nombre d'éléments (~16M) ; on sous-échantillonne par pas
    constant au-delà (les centiles globaux d'un grand rendu sont insensibles au pas).
    """
    if x.numel() == 0:
        return 0.0
    if x.numel() > 8_000_000:
        x = x[:: (x.numel() // 8_000_000 + 1)]
    return float(torch.quantile(x.float(), q))


def _srgb_u8_to_lab(hwc_u8: torch.Tensor) -> torch.Tensor:
    """RGB uint8 sRGB (HWC) → CIELAB (HWC : L* 0-100, a*, b*) sur CUDA."""
    x = hwc_u8.float() / 255.0
    a = 0.055
    lin = torch.where(x <= 0.04045, x / 12.92, ((x + a) / (1.0 + a)) ** 2.4)
    xyz = lin @ _SRGB_LIN_TO_XYZ.T
    t = xyz / _D65
    f = torch.where(t > _LAB_DELTA3, t.clamp_min(0).pow(1.0 / 3.0), _LAB_SLOPE * t + _LAB_OFFSET)
    fx, fy, fz = f[..., 0], f[..., 1], f[..., 2]
    lab = torch.empty_like(xyz)
    lab[..., 0] = 116.0 * fy - 16.0
    lab[..., 1] = 500.0 * (fx - fy)
    lab[..., 2] = 200.0 * (fy - fz)
    return lab


def _hsv_hue_sat(hwc_u8: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Teinte (deg 0-360) et saturation HSV (0-1) d'un RGB uint8 HWC. Torch pur."""
    rgb = hwc_u8.float() / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    cmax = rgb.amax(dim=-1)
    cmin = rgb.amin(dim=-1)
    delta = cmax - cmin
    sat = torch.where(cmax > 1e-6, delta / (cmax + 1e-9), torch.zeros_like(cmax))

    hue = torch.zeros_like(cmax)
    safe = delta > 1e-6
    idx_r = safe & (cmax == r)
    idx_g = safe & (cmax == g) & ~idx_r
    idx_b = safe & (cmax == b) & ~idx_r & ~idx_g
    d = delta + 1e-9
    hue = torch.where(idx_r, ((g - b) / d) % 6.0, hue)
    hue = torch.where(idx_g, ((b - r) / d) + 2.0, hue)
    hue = torch.where(idx_b, ((r - g) / d) + 4.0, hue)
    return (hue * 60.0) % 360.0, sat


# --------------------------------------------------------------------------- #
# 1. Tone (exposition L*)
# --------------------------------------------------------------------------- #
def tone_stats(
    hwc_u8: torch.Tensor, lab: torch.Tensor, mask: torch.Tensor | None = None
) -> ToneStats:
    lstar = lab[..., 0]
    clipped_hi_mask = (hwc_u8 >= _HIGHLIGHT_U8).any(dim=-1)
    clipped_lo_mask = lstar <= _SHADOW_L
    tonal = (~clipped_hi_mask) & (~clipped_lo_mask)
    if mask is not None:
        tonal &= mask

    vals = lstar[tonal]
    if vals.numel() == 0:
        vals = lstar.reshape(-1)
    return ToneStats(
        median_l=_q(vals, 0.5),
        mean_l=float(vals.mean()),
        p05_l=_q(vals, 0.05),
        p95_l=_q(vals, 0.95),
        clipped_hi=float(clipped_hi_mask.float().mean()),
        clipped_lo=float(clipped_lo_mask.float().mean()),
        tonal_frac=float(tonal.float().mean()),
    )


# --------------------------------------------------------------------------- #
# 2. Neutral (cast WB sur quasi-neutres)
# --------------------------------------------------------------------------- #
def neutral_stats(lab: torch.Tensor, mask: torch.Tensor | None = None) -> NeutralStats:
    lstar = lab[..., 0]
    chroma = torch.hypot(lab[..., 1], lab[..., 2])
    neutral_mask = (chroma < _NEUTRAL_CHROMA) & (lstar >= _NEUTRAL_L_MIN) & (lstar <= _NEUTRAL_L_MAX)
    if mask is not None:
        neutral_mask &= mask
    n = int(neutral_mask.sum())
    if n == 0:
        return NeutralStats(0.0, 0.0, 0.0, 0.0, 0)
    a = lab[..., 1][neutral_mask]
    b = lab[..., 2][neutral_mask]
    return NeutralStats(
        a_bias=_q(a, 0.5),
        b_bias=_q(b, 0.5),
        chroma=_q(torch.hypot(a, b), 0.5),
        neutral_frac=float(neutral_mask.float().mean()),
        n_neutral=n,
    )


# --------------------------------------------------------------------------- #
# 3. Bandes HSL
# --------------------------------------------------------------------------- #
def band_stats(
    hwc_u8: torch.Tensor, lab: torch.Tensor, mask: torch.Tensor | None = None
) -> list[BandStats]:
    hue, sat = _hsv_hue_sat(hwc_u8)
    chroma = torch.hypot(lab[..., 1], lab[..., 2])
    lstar = lab[..., 0]

    colored = chroma >= _NEUTRAL_CHROMA
    if mask is not None:
        colored &= mask
    diff = (hue.unsqueeze(-1) - _BAND_CENTERS).abs()
    circ = torch.minimum(diff, 360.0 - diff)
    band_idx = circ.argmin(dim=-1)
    total = hue.numel()

    out: list[BandStats] = []
    for i, name in enumerate(_BAND_NAMES):
        m = colored & (band_idx == i)
        n = int(m.sum())
        if n == 0:
            out.append(BandStats(name, 0.0, float(rm._BAND_CENTERS[i]), 0.0, 0.0, 0.0, 0.0))
            continue
        sat_m = sat[m]
        out.append(
            BandStats(
                name=name,
                frac=float(n / total),
                median_hue=_q(hue[m], 0.5),
                median_chroma=_q(chroma[m], 0.5),
                median_sat=_q(sat_m, 0.5),
                sat_clip_frac=float((sat_m >= 0.97).float().mean()),
                median_l=_q(lstar[m], 0.5),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Composition (équivalent GPU de pipeline.analyze_rendered)
# --------------------------------------------------------------------------- #
def analyze_rendered_gpu(chw_u8: torch.Tensor) -> RenderAnalysis:
    """Analyse un rendu décodé sur GPU (uint8 CHW) en une seule passe Lab CUDA.

    Restreint tone/neutral/bandes à la **zone nette** (top 25% le plus net,
    `sharpness.sharp_mask_gpu` sur L*) — exclut le flou de bokeh/arrière-plan
    de l'histogramme mesuré.
    """
    from . import sharpness

    hwc = _to_hwc_u8(chw_u8)
    lab = _srgb_u8_to_lab(hwc)
    mask = sharpness.sharp_mask_gpu(lab[..., 0])
    return RenderAnalysis(
        tone=tone_stats(hwc, lab, mask=mask),
        neutral=neutral_stats(lab, mask=mask),
        bands=band_stats(hwc, lab, mask=mask),
    )


def analyze_rendered_gpu_dual(chw_u8: torch.Tensor) -> RenderAnalysisDual:
    """Équivalent GPU de `pipeline.analyze_rendered_dual` : global + zone nette.

    Une seule conversion Lab CUDA + une seule carte de netteté, partagées entre les
    deux échelles (global = `mask=None`, sharp = masque net).
    """
    from . import sharpness

    hwc = _to_hwc_u8(chw_u8)
    lab = _srgb_u8_to_lab(hwc)
    mask = sharpness.sharp_mask_gpu(lab[..., 0])
    glob = RenderAnalysis(
        tone=tone_stats(hwc, lab, mask=None),
        neutral=neutral_stats(lab, mask=None),
        bands=band_stats(hwc, lab, mask=None),
    )
    sharp = RenderAnalysis(
        tone=tone_stats(hwc, lab, mask=mask),
        neutral=neutral_stats(lab, mask=mask),
        bands=band_stats(hwc, lab, mask=mask),
    )
    return RenderAnalysisDual(sharp=sharp, glob=glob, mask_sharp_frac=float(mask.float().mean()))
