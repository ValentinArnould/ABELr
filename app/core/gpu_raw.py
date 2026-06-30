"""Décodage RAW **sur GPU** — bayer → ProPhoto linéaire → stats (torch CUDA).

Remplace `raw.load_linear` + `analysis` (chemin LibRaw CPU) par un pipeline GPU :

1. **CPU (mince, irréductible)** : `rawpy` décompresse/déballe le conteneur ARW en
   plan bayer 16-bit et expose les métadonnées (motif CFA, WB as-shot, niveaux noir/blanc,
   matrice couleur). Aucun codec GPU n'existe pour l'ARW Sony → cette étape reste CPU,
   mais ne fait **pas** de demosaic.
2. **GPU (tout le calcul pixel)** : soustraction du niveau de noir + normalisation, WB par
   site CFA, **demosaic** (convolution normalisée = bilinéaire), matrice caméra→ProPhoto
   (réplique la composition dcraw `cam_xyz_coeff`), → RGB float32 linéaire ProPhoto.
   Stats d'exposition (Y) et gray-world calculées sur le tenseur GPU.

Parité avec LibRaw (mêmes primaires ProPhoto, même `use_camera_wb`) : à confirmer par
`tools/validate_gpu_vs_libraw`. La matrice couleur et l'adaptation chromatique sont les
points sensibles — isolés ici pour être ajustables.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
import torch.nn.functional as F

from . import color, gpu
from .analysis import (
    _HIGHLIGHT_CLIP,
    _SHADOW_CLIP,
    ExposureStats,
)
from .render_metrics_gpu import _q

# ProPhoto(D50) → XYZ(D65) : primaires de sortie, adaptées D65 comme la table dcraw.
_PP_TO_XYZ_D65 = (color._BRADFORD_D50_D65 @ color._PP_TO_XYZ_D50).astype(np.float32)

# Noyau de demosaic bilinéaire (convolution normalisée par le compte de voisins).
_BILINEAR_K = torch.tensor(
    [[[[1.0, 2.0, 1.0], [2.0, 4.0, 2.0], [1.0, 2.0, 1.0]]]], dtype=torch.float32
)


@dataclass
class RawBayer:
    """Sortie de l'unpack CPU — **picklable**, vit en RAM hôte (pour le scheduler)."""

    bayer: np.ndarray       # uint16 HxW (zone visible)
    pattern: np.ndarray     # 2x2 int : index couleur par site CFA
    color_desc: str         # ex. "RGBG"
    wb: tuple               # camera_whitebalance [R, G1, B, G2]
    black: tuple            # black_level_per_channel (4)
    white: float            # white_level (scalaire)
    cam_xyz: np.ndarray     # 3x3 : XYZ(D65) → caméra (rgb_xyz_matrix[:3,:3])


@dataclass
class RawGpuResult:
    exposure: ExposureStats
    grayworld_rg: float
    grayworld_bg: float
    asshot_rg: float
    asshot_bg: float


# --------------------------------------------------------------------------- #
# CPU : unpack mince (pas de demosaic)
# --------------------------------------------------------------------------- #
def unpack_raw(path: str) -> RawBayer | None:
    """Déballe le RAW via rawpy (CPU) : bayer + métadonnées. None si illisible.

    Fonction de niveau module → picklable pour un pool de process (cf. `gpu_schedule`).
    """
    import rawpy

    try:
        with rawpy.imread(str(path)) as r:
            bayer = r.raw_image_visible.copy()           # uint16 HxW
            pattern = np.asarray(r.raw_pattern).copy()   # 2x2
            color_desc = r.color_desc.decode("ascii")    # "RGBG"
            wb = tuple(float(x) for x in r.camera_whitebalance)
            black = tuple(float(x) for x in r.black_level_per_channel)
            white = float(r.white_level)
            cam_xyz = np.asarray(r.rgb_xyz_matrix, np.float32)[:3, :3].copy()
    except Exception:
        return None
    return RawBayer(bayer, pattern, color_desc, wb, black, white, cam_xyz)


# --------------------------------------------------------------------------- #
# Matrice caméra → ProPhoto (réplique dcraw cam_xyz_coeff)
# --------------------------------------------------------------------------- #
def _cam_to_prophoto(cam_xyz: np.ndarray) -> np.ndarray:
    """3x3 : caméra-RGB → ProPhoto linéaire.

    dcraw : cam_rgb = cam_xyz · (ProPhoto→XYZ_D65), normalisé en lignes (somme=1, =
    point blanc caméra), puis inversé → caméra→ProPhoto. Reproduit la conversion couleur
    de LibRaw `output_color=ProPhoto`.
    """
    cam_rgb = cam_xyz @ _PP_TO_XYZ_D65          # 3x3 : ProPhoto → caméra
    row_sums = cam_rgb.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    cam_rgb_n = cam_rgb / row_sums              # normalisation point blanc
    return np.linalg.inv(cam_rgb_n).astype(np.float32)  # caméra → ProPhoto


# --------------------------------------------------------------------------- #
# GPU : black-level, WB, demosaic, matrice, stats
# --------------------------------------------------------------------------- #
def _demosaic_bilinear(val: torch.Tensor, chan_map: torch.Tensor) -> torch.Tensor:
    """Demosaic par convolution normalisée. `val` HxW (0-1, WB appliqué), `chan_map`
    HxW dans {0,1,2}. Retourne (3,H,W) caméra-RGB linéaire."""
    K = _BILINEAR_K.to(val.device)
    planes = []
    v4 = val.unsqueeze(0).unsqueeze(0)  # 1,1,H,W
    for c in range(3):
        mask = (chan_map == c).to(val.dtype)
        num = F.conv2d((v4 * mask), K, padding=1)
        den = F.conv2d(mask.unsqueeze(0).unsqueeze(0), K, padding=1)
        planes.append((num / (den + 1e-8)).squeeze(0).squeeze(0))
    return torch.stack(planes, dim=0)  # 3,H,W


def process_bayer_gpu(rb: RawBayer) -> RawGpuResult:
    """Pipeline GPU complet d'un bayer déballé → stats exposition + gray-world."""
    dev = gpu.device()
    H, W = rb.bayer.shape

    bayer = torch.from_numpy(rb.bayer.astype(np.float32)).to(dev)
    pat = torch.from_numpy(rb.pattern.astype(np.int64)).to(dev)          # 2x2
    idx = pat.repeat((H + 1) // 2, (W + 1) // 2)[:H, :W]                 # HxW index 0..3

    black_v = torch.tensor(rb.black, dtype=torch.float32, device=dev)    # (4,)
    # WB normalisée au vert (index 1) : neutre → (g,g,g).
    wb_arr = torch.tensor(rb.wb, dtype=torch.float32, device=dev)
    green = wb_arr[1] if wb_arr[1] != 0 else torch.tensor(1.0, device=dev)
    wb_norm = wb_arr / green

    black_map = black_v[idx]
    denom = (rb.white - black_map).clamp_min(1.0)
    val = ((bayer - black_map).clamp_min(0.0) / denom) * wb_norm[idx]    # HxW, WB appliqué

    # index couleur CFA → canal RGB 0/1/2 via color_desc.
    letter_to_c = {"R": 0, "G": 1, "B": 2}
    chan_of_index = torch.tensor(
        [letter_to_c[rb.color_desc[i]] for i in range(len(rb.color_desc))],
        dtype=torch.int64, device=dev,
    )
    chan_map = chan_of_index[idx]                                        # HxW in {0,1,2}

    cam_rgb = _demosaic_bilinear(val, chan_map)                         # 3,H,W caméra
    M = torch.from_numpy(_cam_to_prophoto(rb.cam_xyz)).to(dev)          # 3x3 caméra→ProPhoto
    # (3,H,W) → (H*W,3) @ M.T → ProPhoto, clampé [0,1] (parité LibRaw output range).
    flat = cam_rgb.reshape(3, -1).T                                     # N,3
    pp = (flat @ M.T).clamp(0.0, 1.0)                                   # N,3 ProPhoto linéaire

    # Exposition (Y de XYZ ProPhoto) — mêmes poids/seuils que analysis.exposure_stats.
    y_w = torch.tensor(color.PROPHOTO_TO_Y, dtype=torch.float32, device=dev)
    luma = pp @ y_w                                                     # N
    total = luma.numel()
    exposure = ExposureStats(
        mean_luma=float(luma.mean()),
        median_luma=_q(luma, 0.5),
        clipped_highlights=float((pp >= _HIGHLIGHT_CLIP).any(dim=-1).sum()) / total,
        clipped_shadows=float((luma <= _SHADOW_CLIP).sum()) / total,
    )
    # Gray-world ProPhoto (g/r, g/b) — comme analysis.gray_world_wb.
    mean_rgb = pp.mean(dim=0) + 1e-9
    grayworld_rg = float(mean_rgb[1] / mean_rgb[0])
    grayworld_bg = float(mean_rgb[1] / mean_rgb[2])

    g = rb.wb[1] or 1.0
    return RawGpuResult(
        exposure=exposure,
        grayworld_rg=grayworld_rg,
        grayworld_bg=grayworld_bg,
        asshot_rg=rb.wb[0] / g,
        asshot_bg=rb.wb[2] / g,
    )


def analyze_raw_gpu(path: str) -> RawGpuResult | None:
    """Unpack CPU + traitement GPU d'un RAW. None si illisible."""
    rb = unpack_raw(path)
    if rb is None:
        return None
    return process_bayer_gpu(rb)
