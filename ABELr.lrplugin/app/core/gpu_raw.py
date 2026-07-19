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

from . import color, gpu, render_metrics_gpu, sharpness
from .analysis import (
    _HIGHLIGHT_CLIP,
    _SHADOW_CLIP,
    ExposureStats,
)
from .render_metrics import BandStats, ToneStats
from .render_metrics_gpu import _q

# ProPhoto(D50) linéaire → sRGB(D65) linéaire — même matrice que color.PROPHOTO_TO_SRGB,
# utilisée ici pour donner au RAW une représentation sRGB u8 comparable (Lab/bandes) à
# InCameraJPEG/PreviewJPEG, sans jamais servir à l'analyse d'exposition/WB (ProPhoto seul).
_PP_TO_SRGB = torch.from_numpy(color.PROPHOTO_TO_SRGB)

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
    exposure: ExposureStats               # exposition GLOBAL (frame entier, Y ProPhoto)
    grayworld_rg: float                   # gray-world GLOBAL
    grayworld_bg: float
    asshot_rg: float
    asshot_bg: float
    tone: ToneStats | None = None         # zone nette, sRGB dérivé du RAW
    bands: list[BandStats] | None = None  # zone nette, sRGB dérivé du RAW
    exposure_sharp: ExposureStats | None = None  # exposition ZONE NETTE (masque Laplacien)
    grayworld_rg_sharp: float | None = None       # gray-world ZONE NETTE
    grayworld_bg_sharp: float | None = None
    mask_sharp_frac: float | None = None          # fraction de pixels retenus (diagnostic)


def _prophoto_linear_to_srgb_u8_gpu(pp_hw3: torch.Tensor) -> torch.Tensor:
    """ProPhoto linéaire (H,W,3) CUDA → sRGB uint8 (H,W,3) CUDA. Affichage/comparaison
    histogramme uniquement (jamais pour l'exposition/WB, qui restent en ProPhoto)."""
    M = _PP_TO_SRGB.to(pp_hw3.device)
    srgb_lin = (pp_hw3 @ M.T).clamp(0.0, 1.0)
    a = 0.055
    srgb = torch.where(
        srgb_lin <= 0.0031308, 12.92 * srgb_lin, (1 + a) * srgb_lin.clamp_min(0).pow(1 / 2.4) - a
    )
    return (srgb.clamp(0.0, 1.0) * 255.0).round().to(torch.uint8)


# --------------------------------------------------------------------------- #
# CPU : unpack mince (pas de demosaic)
# --------------------------------------------------------------------------- #
def bayer_from_open(r) -> RawBayer:
    """RawBayer depuis un handle rawpy DÉJÀ ouvert.

    Extrait pour l'unpack unifié du scheduler (revue Fable 5 P-02) : la même
    ouverture rawpy sert au bayer ET au JPEG boîtier (`embedded_jpeg.extract_from_open`).
    """
    bayer = r.raw_image_visible.copy()           # uint16 HxW
    pattern = np.asarray(r.raw_pattern).copy()   # 2x2
    color_desc = r.color_desc.decode("ascii")    # "RGBG"
    wb = tuple(float(x) for x in r.camera_whitebalance)
    black = tuple(float(x) for x in r.black_level_per_channel)
    white = float(r.white_level)
    cam_xyz = np.asarray(r.rgb_xyz_matrix, np.float32)[:3, :3].copy()
    return RawBayer(bayer, pattern, color_desc, wb, black, white, cam_xyz)


def unpack_raw(path: str) -> RawBayer | None:
    """Déballe le RAW via rawpy (CPU) : bayer + métadonnées. None si illisible.

    Fonction de niveau module → picklable pour un pool de process (cf. `gpu_schedule`).
    """
    import rawpy

    try:
        with rawpy.imread(str(path)) as r:
            return bayer_from_open(r)
    except Exception:
        return None


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

    # H2D en uint16 (48 Mo) puis cast float32 SUR GPU — au lieu d'un astype float32
    # côté CPU qui doublait le trafic PCIe et allouait 96 Mo hôte (revue Fable 5 P-06).
    bayer = torch.from_numpy(rb.bayer).to(dev).to(torch.float32)
    pat = torch.from_numpy(rb.pattern.astype(np.int64)).to(dev)          # 2x2
    idx = pat.repeat((H + 1) // 2, (W + 1) // 2)[:H, :W]                 # HxW index 0..3

    black_v = torch.tensor(rb.black, dtype=torch.float32, device=dev)    # (4,)
    # WB normalisée au vert (index 1) : neutre → (g,g,g).
    # Convention dcraw/LibRaw : cam_mul[G2]==0 signifie « G2 = G1 » — sans cette
    # garde les sites G2 seraient multipliés par 0 (canal vert faussé au demosaic).
    # No-op sur Sony ARW (G2=G1 déjà), casse d'autres boîtiers sinon (C-01).
    wb = list(rb.wb)
    if len(wb) > 3 and wb[3] == 0:
        wb[3] = wb[1]
    wb_arr = torch.tensor(wb, dtype=torch.float32, device=dev)
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

    def _exposure(pp_sub: torch.Tensor, luma_sub: torch.Tensor) -> ExposureStats:
        """ExposureStats sur un sous-ensemble de pixels (global ou zone nette)."""
        n = luma_sub.numel()
        if n == 0:
            return ExposureStats(0.0, 0.0, 0.0, 0.0)
        return ExposureStats(
            mean_luma=float(luma_sub.mean()),
            median_luma=_q(luma_sub, 0.5),
            clipped_highlights=float((pp_sub >= _HIGHLIGHT_CLIP).any(dim=-1).sum()) / n,
            clipped_shadows=float((luma_sub <= _SHADOW_CLIP).sum()) / n,
        )

    def _grayworld(pp_sub: torch.Tensor) -> tuple[float, float]:
        """Gray-world ProPhoto (g/r, g/b) — comme analysis.gray_world_wb."""
        if pp_sub.numel() == 0:
            return 0.0, 0.0
        mean_rgb = pp_sub.mean(dim=0) + 1e-9
        return float(mean_rgb[1] / mean_rgb[0]), float(mean_rgb[1] / mean_rgb[2])

    # Global (frame entier).
    exposure = _exposure(pp, luma)
    grayworld_rg, grayworld_bg = _grayworld(pp)

    # Tone/bandes zone nette — sRGB dérivé du ProPhoto, comparable aux JPEG (boîtier/aperçu).
    pp_hw3 = pp.reshape(H, W, 3)
    hwc_u8 = _prophoto_linear_to_srgb_u8_gpu(pp_hw3)
    lab = render_metrics_gpu._srgb_u8_to_lab(hwc_u8)
    sharp = sharpness.sharp_mask_gpu(lab[..., 0])                       # HxW bool
    tone = render_metrics_gpu.tone_stats(hwc_u8, lab, mask=sharp)
    bands = render_metrics_gpu.band_stats(hwc_u8, lab, mask=sharp)

    # Zone nette (mêmes réductions Y/gray-world, restreintes au masque net).
    mask_flat = sharp.reshape(-1)
    exposure_sharp = _exposure(pp[mask_flat], luma[mask_flat])
    grayworld_rg_sharp, grayworld_bg_sharp = _grayworld(pp[mask_flat])
    mask_sharp_frac = float(sharp.float().mean())

    g = rb.wb[1] or 1.0
    return RawGpuResult(
        exposure=exposure,
        grayworld_rg=grayworld_rg,
        grayworld_bg=grayworld_bg,
        asshot_rg=rb.wb[0] / g,
        asshot_bg=rb.wb[2] / g,
        tone=tone,
        bands=bands,
        exposure_sharp=exposure_sharp,
        grayworld_rg_sharp=grayworld_rg_sharp,
        grayworld_bg_sharp=grayworld_bg_sharp,
        mask_sharp_frac=mask_sharp_frac,
    )


def analyze_raw_gpu(path: str) -> RawGpuResult | None:
    """Unpack CPU + traitement GPU d'un RAW. None si illisible."""
    rb = unpack_raw(path)
    if rb is None:
        return None
    return process_bayer_gpu(rb)
