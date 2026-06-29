"""Analyse exposition / balance des blancs (numpy).

Deux sources d'entrée :
- **RAW ProPhoto linéaire** : `exposure_stats` / `gray_world_wb` — source physique,
  float32 produit par `image_source.load_for_analysis`. Indépendant du style appliqué.
- **Preview JPEG rendue** : `analyze_preview_jpeg` — rendu Lr (profil + presets cuits),
  sRGB display-referred u8. Encode le résultat visuel réel. Meilleure corrélation avec
  l'exposition choisie par l'utilisateur (r=0.937 sur vérité terrain n=10, vs 0.914 RAW).
  WB masquée sur tons moyens (validée sur previews exposées — invalide à expo ≈ 0).

Métriques consommées par `gui.analysis_worker` (affichage). Le calcul des
corrections WB/expo vit dans `core.wb_model` / `core.seeds`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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


# Poids luma Rec.709 (sRGB display) — utilisés sur les previews JPEG.
_REC709 = np.array([0.2126, 0.7152, 0.0722], np.float32)


@dataclass
class PreviewStats:
    """Métriques d'une preview JPEG rendue (espace display sRGB).

    disp_median / disp_mean : luma display gamma-encodée (0-1, perceptuelle).
    lin_median / lin_mean   : luma linéarisée (sRGB → linéaire, plus proche du signal).
    mid_frac                : fraction de pixels de tons moyens (lin 0.05-0.6 → WB fiable).
    gw_rg / gw_bg           : gray-world g/r, g/b sur tons moyens masqués.
                              Invalide si mid_frac < 0.02 (photo trop sombre → expo d'abord).
    """

    disp_median: float
    disp_mean: float
    lin_median: float
    lin_mean: float
    mid_frac: float
    gw_rg: float
    gw_bg: float


def _srgb_u8_to_linear(u8: np.ndarray) -> np.ndarray:
    """sRGB uint8 → float32 linéaire [0, 1] (courbe de transfert inverse sRGB)."""
    x = u8.astype(np.float32) / 255.0
    a = 0.055
    return np.where(x <= 0.04045, x / 12.92, ((x + a) / (1.0 + a)) ** 2.4)


def analyze_preview_jpeg(path: str | Path) -> PreviewStats:
    """Analyse une preview JPEG rendue par Lr : exposition + WB masquée tons moyens.

    La preview est sRGB display-referred (profil + presets cuits) → encode le rendu
    visuel réel, indépendant du RAW source. Meilleure corrélation avec l'exposition
    choisie par l'utilisateur que le RAW seul (r=0.937 vs 0.914, n=10).

    **Ordre obligatoire** : toujours appeler APRÈS une correction d'exposition correcte.
    À expo ≈ 0 sur photos sombres (nuit), mid_frac peut être < 2% → gw_rg/bg non fiables
    (gray-world explose sur pixels noirs). Le champ `mid_frac` permet de détecter ce cas.

    Accepte deux types de fichiers :
    - Fichier `.jpg` standard (miniature de requestJpegThumbnail) → décodé directement.
    - Fichier preview Lr sans extension (`Previews.lrdata`) → SOI-seeking pour l'en-tête
      AgHg (même logique que `previews.decode_rendered_preview`).

    Lève ValueError si le fichier n'existe pas ou ne se décode pas.
    """
    import cv2

    _JPEG_SOI = b"\xff\xd8\xff"
    p = Path(path)
    if not p.exists():
        raise ValueError(f"Preview introuvable : {p}")

    # Lecture brute + détection du flux JPEG (gère l'en-tête AgHg des .lrfprev
    # et les fichiers sans extension de Previews.lrdata, comme cv2.imread ne
    # peut pas les identifier par extension).
    data = p.read_bytes()
    start = 0 if data[:3] == _JPEG_SOI else data.find(_JPEG_SOI)
    if start == -1:
        raise ValueError(f"Aucun flux JPEG dans {p}")
    arr = np.frombuffer(data, np.uint8, offset=start)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Décodage JPEG échoué : {p}")
    rgb = img[:, :, ::-1]  # BGR → RGB uint8

    lin = _srgb_u8_to_linear(rgb)
    disp_luma = (rgb.astype(np.float32) / 255.0) @ _REC709
    lin_luma = lin @ _REC709

    # Masque tons moyens (linéaire 0.05–0.6) pour gray-world fiable.
    mid = (lin_luma > 0.05) & (lin_luma < 0.6)
    mid_frac = float(mid.mean())
    if mid_frac >= 0.02:
        sub = lin[mid]
    else:
        # Repli : pixels les plus lumineux (au moins quelque chose à mesurer).
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
