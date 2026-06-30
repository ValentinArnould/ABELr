"""Métriques en **espace rendu** (output-referred) — fondation de la refonte.

Le RAW scène-linéaire (`raw.py` / `analysis.py`) mesure la physique de la scène.
Mais l'exposition perçue et l'équilibre couleur que le photographe juge vivent dans
le **rendu** : après profil DCP + courbe de tons + curseurs, encodé sRGB display.
Ce module mesure donc sur le **JPEG rendu** (aperçu Lr ou `requestJpegThumbnail`),
décodé en RGB uint8 sRGB.

Trois familles de mesures, toutes consommées par exposure / wb (raffinement) / hsl :

1. `tone_stats`  — clarté perçue **CIE L*** robuste (médiane tons-moyens, écrêtage exclu)
                   → cible et écart d'exposition.
2. `neutral_stats` — biais a*/b* résiduel sur les **pixels quasi-neutres** seulement
                   → raffinement WB (jamais de gray-world global, cf. impasse n=1142).
3. `band_stats`  — chroma / clarté / teinte par **bande de teinte HSL** (8 canaux Lr)
                   → planification HSL (sursaturation, luminance, recentrage hue).

Colorimétrie : sRGB (IEC 61966-2-1) → XYZ(D65) → CIELAB(D65). Constantes standard,
non inventées. Les **centres de bande** HSL et la réponse des curseurs sont *nominaux*
ici (mesure) ; leur calage précis se fait dans `response.py` + scripts de validation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# --------------------------------------------------------------------------- #
# Colorimétrie sRGB → CIELAB (D65). Matrices/constantes standard.
# --------------------------------------------------------------------------- #
# sRGB linéaire (primaires Rec.709, blanc D65) → XYZ. IEC 61966-2-1.
_SRGB_LIN_TO_XYZ_D65 = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    np.float32,
)

# Blanc de référence D65 (CIE 1931, 2°).
_D65_WHITE = np.array([0.95047, 1.0, 1.08883], np.float32)

# Seuil/pente de la fonction f() de CIELAB (δ = 6/29).
_LAB_DELTA = 6.0 / 29.0
_LAB_DELTA3 = _LAB_DELTA**3
_LAB_SLOPE = 1.0 / (3.0 * _LAB_DELTA**2)  # = 7.787...
_LAB_OFFSET = 4.0 / 29.0


def srgb_u8_to_linear(u8: np.ndarray) -> np.ndarray:
    """sRGB uint8 → float32 linéaire [0, 1] (EOTF inverse sRGB)."""
    x = u8.astype(np.float32) / 255.0
    a = 0.055
    return np.where(x <= 0.04045, x / 12.92, ((x + a) / (1.0 + a)) ** 2.4)


def _lab_f(t: np.ndarray) -> np.ndarray:
    return np.where(t > _LAB_DELTA3, np.cbrt(t), _LAB_SLOPE * t + _LAB_OFFSET)


def srgb_u8_to_lab(rgb_u8: np.ndarray) -> np.ndarray:
    """RGB uint8 sRGB (HxWx3, ordre RGB) → CIELAB (HxWx3 : L* 0-100, a*, b*)."""
    lin = srgb_u8_to_linear(rgb_u8)
    xyz = lin @ _SRGB_LIN_TO_XYZ_D65.T
    f = _lab_f(xyz / _D65_WHITE)
    fx, fy, fz = f[..., 0], f[..., 1], f[..., 2]
    lab = np.empty_like(xyz)
    lab[..., 0] = 116.0 * fy - 16.0
    lab[..., 1] = 500.0 * (fx - fy)
    lab[..., 2] = 200.0 * (fy - fz)
    return lab


# --------------------------------------------------------------------------- #
# 1. Exposition — clarté perçue L* robuste
# --------------------------------------------------------------------------- #
# Pixel « écrêté hautes lumières » : un canal sRGB quasi saturé (ciel/spéculaire).
_HIGHLIGHT_U8 = 250
# Plancher d'ombre en L* : en dessous, le pixel ne porte pas d'info tonale utile.
_SHADOW_L = 5.0


@dataclass
class ToneStats:
    """Clarté perçue d'un rendu (CIE L*, 0-100).

    median_l    : médiane L* des pixels **tonaux** (hors écrêtage HL / ombres mortes).
                  Métrique d'exposition principale (cible = médiane des seeds).
    mean_l      : moyenne L* tonale (clé photographique, complément).
    p05_l/p95_l : 5e/95e centiles L* tonaux (étalement tonal).
    clipped_hi  : fraction de pixels à canal sRGB ≥ 250 (brûlés).
    clipped_lo  : fraction de pixels à L* ≤ 5 (bouchés).
    tonal_frac  : fraction de pixels retenus comme tonaux.
    """

    median_l: float
    mean_l: float
    p05_l: float
    p95_l: float
    clipped_hi: float
    clipped_lo: float
    tonal_frac: float


def tone_stats(rgb_u8: np.ndarray, lab: np.ndarray | None = None) -> ToneStats:
    """Clarté perçue robuste d'un RGB uint8 sRGB rendu.

    Exclut les hautes lumières écrêtées (ciel, spéculaire) et les ombres mortes, qui
    ne reflètent pas le niveau d'exposition voulu, puis statistiques sur le reste.
    `lab` peut être fourni pour éviter une reconversion (sinon calculé).
    """
    if lab is None:
        lab = srgb_u8_to_lab(rgb_u8)
    lstar = lab[..., 0]
    total = lstar.size

    clipped_hi_mask = (rgb_u8 >= _HIGHLIGHT_U8).any(axis=-1)
    clipped_lo_mask = lstar <= _SHADOW_L
    tonal = ~clipped_hi_mask & ~clipped_lo_mask

    vals = lstar[tonal]
    if vals.size == 0:  # rendu entièrement écrêté : repli sur tout
        vals = lstar.reshape(-1)
    return ToneStats(
        median_l=float(np.median(vals)),
        mean_l=float(vals.mean()),
        p05_l=float(np.percentile(vals, 5)),
        p95_l=float(np.percentile(vals, 95)),
        clipped_hi=float(clipped_hi_mask.mean()),
        clipped_lo=float(clipped_lo_mask.mean()),
        tonal_frac=float(tonal.mean()),
    )


# --------------------------------------------------------------------------- #
# 2. WB — biais résiduel sur pixels quasi-neutres
# --------------------------------------------------------------------------- #
# Chroma max (C* = hypot(a*, b*)) pour qu'un pixel compte comme « neutre ».
_NEUTRAL_CHROMA = 10.0
# Fenêtre de clarté des neutres exploitables (évite noir bruité / blanc écrêté).
_NEUTRAL_L_MIN, _NEUTRAL_L_MAX = 20.0, 92.0


@dataclass
class NeutralStats:
    """Cast résiduel mesuré sur les pixels quasi-neutres d'un rendu.

    a_bias / b_bias : médiane a*/b* des neutres (cible = 0 → pas de cast).
                      a*>0 = magenta, a*<0 = vert ; b*>0 = jaune, b*<0 = bleu.
    chroma          : médiane C* des neutres (résidu de cast en magnitude).
    neutral_frac    : fraction de pixels jugés neutres (fiabilité du raffinement WB).
    n_neutral       : nombre de pixels neutres.
    """

    a_bias: float
    b_bias: float
    chroma: float
    neutral_frac: float
    n_neutral: int


def neutral_stats(
    lab: np.ndarray,
    chroma_max: float = _NEUTRAL_CHROMA,
    l_min: float = _NEUTRAL_L_MIN,
    l_max: float = _NEUTRAL_L_MAX,
) -> NeutralStats:
    """Mesure le cast résiduel **sur les neutres seulement**.

    Ne fait **jamais** de gray-world global (contaminé par le contenu — impasse
    prouvée n=1142). Le caller décide via `neutral_frac` si le raffinement WB est
    fiable ; sinon il garde la prédiction seed.
    """
    lstar = lab[..., 0]
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    mask = (chroma < chroma_max) & (lstar >= l_min) & (lstar <= l_max)
    n = int(mask.sum())
    if n == 0:
        return NeutralStats(0.0, 0.0, 0.0, 0.0, 0)
    a = lab[..., 1][mask]
    b = lab[..., 2][mask]
    return NeutralStats(
        a_bias=float(np.median(a)),
        b_bias=float(np.median(b)),
        chroma=float(np.median(np.hypot(a, b))),
        neutral_frac=float(mask.mean()),
        n_neutral=n,
    )


# --------------------------------------------------------------------------- #
# 3. HSL — statistiques par bande de teinte (8 canaux Lr)
# --------------------------------------------------------------------------- #
# Ordre Lr des 8 bandes HSL.
BAND_NAMES = ("Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta")
# Centres de teinte NOMINAUX (degrés HSV) — approximatifs. Les frontières exactes et
# la réponse des curseurs sont calées dans response.py / scripts de validation.
_BAND_CENTERS = np.array([0.0, 35.0, 60.0, 135.0, 180.0, 225.0, 275.0, 315.0], np.float32)
# Population minimale d'une bande pour que ses stats soient exploitables.
_BAND_MIN_FRAC = 0.01


def rgb_u8_to_hsv_hue_sat(rgb_u8: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Teinte (degrés 0-360) et saturation HSV (0-1) d'un RGB uint8. Numpy pur."""
    rgb = rgb_u8.astype(np.float32) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    cmax = rgb.max(axis=-1)
    cmin = rgb.min(axis=-1)
    delta = cmax - cmin
    sat = np.where(cmax > 1e-6, delta / (cmax + 1e-9), 0.0)

    hue = np.zeros_like(cmax)
    safe = delta > 1e-6
    # Secteur dominé par R / G / B.
    idx_r = safe & (cmax == r)
    idx_g = safe & (cmax == g) & ~idx_r
    idx_b = safe & (cmax == b) & ~idx_r & ~idx_g
    hue[idx_r] = (((g - b) / (delta + 1e-9)) % 6.0)[idx_r]
    hue[idx_g] = (((b - r) / (delta + 1e-9)) + 2.0)[idx_g]
    hue[idx_b] = (((r - g) / (delta + 1e-9)) + 4.0)[idx_b]
    return (hue * 60.0) % 360.0, sat


def _nearest_band(hue_deg: np.ndarray) -> np.ndarray:
    """Index 0-7 de la bande la plus proche par distance circulaire de teinte."""
    diff = np.abs(hue_deg[..., None] - _BAND_CENTERS[None, :])
    circ = np.minimum(diff, 360.0 - diff)
    return circ.argmin(axis=-1)


@dataclass
class BandStats:
    """Statistiques d'une bande de teinte HSL sur un rendu.

    name        : nom Lr de la bande (clé du curseur).
    frac        : fraction de pixels (population — fiabilité).
    median_hue  : teinte médiane HSV (degrés) — dérive vs centre nominal.
    median_chroma : chroma médiane CIELAB C* (mesure perceptuelle de saturation).
    median_sat  : saturation HSV médiane (0-1) — proxy rapide.
    sat_clip_frac : fraction de pixels quasi-saturés (S ≥ 0.97) — sursaturation.
    median_l    : clarté médiane L* de la bande.
    """

    name: str
    frac: float
    median_hue: float
    median_chroma: float
    median_sat: float
    sat_clip_frac: float
    median_l: float


def band_stats(
    rgb_u8: np.ndarray,
    lab: np.ndarray | None = None,
    min_chroma: float = _NEUTRAL_CHROMA,
) -> list[BandStats]:
    """Stats par bande HSL. Les pixels quasi-neutres (C* < `min_chroma`) sont exclus
    (la teinte d'un gris n'a pas de sens). Renvoie 8 `BandStats` (bandes vides → frac=0).
    """
    if lab is None:
        lab = srgb_u8_to_lab(rgb_u8)
    hue, sat = rgb_u8_to_hsv_hue_sat(rgb_u8)
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    lstar = lab[..., 0]

    colored = chroma >= min_chroma
    band_idx = _nearest_band(hue)
    total = hue.size

    out: list[BandStats] = []
    for i, name in enumerate(BAND_NAMES):
        m = colored & (band_idx == i)
        n = int(m.sum())
        if n == 0:
            out.append(BandStats(name, 0.0, float(_BAND_CENTERS[i]), 0.0, 0.0, 0.0, 0.0))
            continue
        out.append(
            BandStats(
                name=name,
                frac=float(n / total),
                median_hue=float(np.median(hue[m])),
                median_chroma=float(np.median(chroma[m])),
                median_sat=float(np.median(sat[m])),
                sat_clip_frac=float((sat[m] >= 0.97).mean()),
                median_l=float(np.median(lstar[m])),
            )
        )
    return out


def band_is_reliable(band: BandStats, min_frac: float = _BAND_MIN_FRAC) -> bool:
    """Bande exploitable pour une correction HSL (population suffisante)."""
    return band.frac >= min_frac
