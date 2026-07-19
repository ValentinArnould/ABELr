"""Décodage RAW Sony ARW via rawpy (LibRaw).

Chemin de repli de l'analyse : utilisé par `image_source` quand aucune Smart
Preview n'existe pour la photo.

Deux sorties :
- `load_linear` : float32 0-1 **scène-linéaire**, primaires **ProPhoto** (gamut
  large) → espace de travail de l'analyse (exposition, balance des blancs). Pas de
  gamma, pas d'écrêtage 8-bit ni gamut, point de clipping capteur préservé.
- `load_rgb` : uint8 sRGB display-referred → affichage GUI / vignettes.

Choix `use_camera_wb=True` et `output_color=ProPhoto` validés par calibration sur
catalogue réel (cf. `core/color`). L'analyse proprement dite vit dans `analysis.py`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rawpy

from . import color


# Réglages communs aux deux décodages :
# - `no_auto_bright` : pas d'auto-exposition, sinon la luma mesurée n'a aucun sens.
# - `use_camera_wb` : applique la balance des blancs « as shot ». Indispensable —
#   sans elle, les ratios par canal mesurent le déséquilibre spectral du capteur,
#   pas la scène (gray-world ininterprétable). Avec, le gray-world mesure le cast
#   résiduel vs as-shot = le signal utile.
_COMMON = dict(no_auto_bright=True, use_camera_wb=True)


def load_linear(
    path: str | Path,
    half_size: bool = True,
    color_space: str = color.ANALYSIS_COLOR_SPACE,
) -> np.ndarray:
    """Décode un RAW (.ARW) en RGB **float32 0-1 scène-linéaire**.

    `gamma=(1, 1)` désactive la courbe de transfert ; `output_color` fixe les
    primaires (défaut ProPhoto, gamut large — évite que l'écrêtage gamut sRGB
    biaise la balance des blancs). `half_size` accélère le décodage (¼ des pixels)
    sans rien changer aux statistiques globales.

    Retourne un array HxWx3 float32 dans [0, 1].
    """
    with rawpy.imread(str(path)) as raw:
        rgb16 = raw.postprocess(
            half_size=half_size,
            output_bps=16,
            gamma=(1, 1),
            output_color=getattr(rawpy.ColorSpace, color_space),
            **_COMMON,
        )
    return rgb16.astype(np.float32) / 65535.0


def load_rgb(path: str | Path, half_size: bool = True) -> np.ndarray:
    """Décode un RAW (.ARW) en RGB **uint8 sRGB** display-referred (affichage GUI).

    Gamma sRGB par défaut. Pour l'analyse, préférer `load_linear`.
    Retourne un array HxWx3 uint8.
    """
    with rawpy.imread(str(path)) as raw:
        rgb = raw.postprocess(half_size=half_size, output_bps=8, **_COMMON)
    return rgb


def read_asshot_wb(path: str | Path) -> tuple[float, float]:
    """Lit la balance des blancs « as shot » du boîtier (multiplicateurs RGGB).

    Retourne (r/g, b/g) — les ratios normalisés au vert du WB caméra, signal
    physique d'entrée du modèle WB (`core.wb_model`). Ancré sur le boîtier : pour
    un même boîtier ces ratios prédisent la Temperature choisie (pente quasi-
    universelle, cf. calibration ILCE-7M4). Indépendant du contenu de la scène.
    """
    with rawpy.imread(str(path)) as raw:
        wb = list(raw.camera_whitebalance)  # [R, G1, B, G2]
    g = wb[1] or 1.0
    return wb[0] / g, wb[2] / g


def load_thumbnail(path: str | Path) -> np.ndarray | None:
    """Extrait la miniature JPEG embarquée si disponible (rapide, pour aperçu GUI)."""
    try:
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
    except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
        return None
    if thumb.format == rawpy.ThumbFormat.JPEG:
        import cv2

        data = np.frombuffer(thumb.data, dtype=np.uint8)
        bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if bgr is None:  # JPEG embarqué corrompu (revue Fable 5 C-03)
            return None
        return bgr[:, :, ::-1]  # BGR->RGB
    if thumb.format == rawpy.ThumbFormat.BITMAP:
        return thumb.data
    return None
