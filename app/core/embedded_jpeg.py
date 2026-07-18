"""JPEG embarqué boîtier — prior d'exposition (et look initial).

Chaque ARW contient le JPEG rendu **par le boîtier** : c'est l'exposition jugée
bonne à la prise de vue + le premier look (Creative Look Sony). sRGB display-referred.
On s'en sert comme **repli de cible d'exposition** quand les seeds manquent (décision
utilisateur : seeds d'abord, JPEG boîtier ensuite).

Extraction via `raw.load_thumbnail` (LibRaw `extract_thumb`) ; mesure de clarté via
`render_metrics.tone_stats` (CIE L*, même métrique que le rendu LR → comparable).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import raw, render_metrics
from .pipeline import RenderAnalysis
from .render_metrics import BandStats, ToneStats

# Plafond dur du nombre de process de lecture RAW (anti-gel). Même sur une grosse
# machine, au-delà la RAM (un interpréteur spawné + buffers par process) explose.
_MAX_RAW_WORKERS = 8


def load_embedded_rgb(path: str | Path) -> np.ndarray | None:
    """RGB uint8 sRGB du JPEG embarqué boîtier, ou None si absent/illisible."""
    try:
        thumb = raw.load_thumbnail(path)
    except Exception:
        return None
    if thumb is None:
        return None
    arr = np.asarray(thumb)
    if arr.ndim != 3 or arr.shape[-1] != 3:
        return None
    return arr.astype(np.uint8, copy=False)


def embedded_tone(path: str | Path) -> ToneStats | None:
    """Clarté perçue (CIE L*) du JPEG embarqué boîtier, ou None.

    Même `tone_stats` que sur le rendu LR → la médiane L* est directement comparable
    à la cible d'exposition. Sert de prior quand peu/pas de seeds.
    """
    rgb = load_embedded_rgb(path)
    if rgb is None:
        return None
    return render_metrics.tone_stats(rgb)


def embedded_target_l(path: str | Path) -> float | None:
    """Médiane L* du JPEG boîtier (cible d'exposition de repli), ou None."""
    ts = embedded_tone(path)
    return ts.median_l if ts is not None else None


# --------------------------------------------------------------------------- #
# Lecture RAW combinée (UNE ouverture rawpy) + lot parallèle
# --------------------------------------------------------------------------- #
@dataclass
class RawReference:
    """Tout ce qu'on tire d'une photo via UNE ouverture du RAW.

    embedded_tone / embedded_bands : mesures du JPEG boîtier zone nette (cibles mode
                                     embedded ; = `sharp.tone`/`sharp.bands`).
    asshot_rg / asshot_bg          : WB as-shot (entrée du modèle WB seeds).
    sharp / glob                   : analyse complète (tone+neutral+bandes) zone nette
                                     et globale du JPEG boîtier (chemin GPU dual).
    mask_sharp_frac                : fraction de pixels retenus par le masque net.
    """

    embedded_tone: ToneStats | None
    embedded_bands: list[BandStats] | None
    asshot_rg: float | None
    asshot_bg: float | None
    sharp: RenderAnalysis | None = None
    glob: RenderAnalysis | None = None
    mask_sharp_frac: float | None = None


def read_raw_reference(path: str | Path) -> RawReference:
    """Ouvre le RAW **une seule fois** → JPEG boîtier (tone+bandes) + WB as-shot.

    Fonction de niveau module → picklable pour `ProcessPoolExecutor`. L'ouverture
    rawpy (~quelques secondes/photo) est le coût dominant : la faire une fois pour
    les deux usages, et paralléliser, est le bon levier perf (cf. `read_raw_references`).
    """
    import rawpy

    try:
        with rawpy.imread(str(path)) as r:
            wb = list(r.camera_whitebalance)  # [R, G1, B, G2]
            try:
                thumb = r.extract_thumb()
                jpeg = thumb.data if thumb.format == rawpy.ThumbFormat.JPEG else None
            except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
                jpeg = None
    except Exception:
        return RawReference(None, None, None, None)

    g = wb[1] or 1.0
    asshot_rg, asshot_bg = wb[0] / g, wb[2] / g

    tone = bands = None
    if jpeg:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            rgb = img[:, :, ::-1]  # BGR → RGB
            lab = render_metrics.srgb_u8_to_lab(rgb)
            tone = render_metrics.tone_stats(rgb, lab)
            bands = render_metrics.band_stats(rgb, lab)
    return RawReference(tone, bands, asshot_rg, asshot_bg)


def _bounded_workers(n_paths: int) -> int:
    """Nombre de process **borné** pour le lot.

    CRITIQUE : `ProcessPoolExecutor(max_workers=None)` lançait `min(32, cpu+4)`
    process, chacun important rawpy/numpy/cv2 (spawn Windows) + ouvrant un RAW →
    saturation CPU/RAM = gel du PC. On borne aux **cœurs physiques** (heuristique
    HT = logiques // 2), plafonné, et jamais plus que le nombre de photos.
    """
    logical = os.cpu_count() or 4
    physical = max(1, logical // 2)
    return max(1, min(n_paths, physical, _MAX_RAW_WORKERS))


@dataclass
class EmbeddedExtract:
    """Sortie de l'unpack CPU embedded — **picklable**, octets JPEG **non décodés**.

    Le décodage du JPEG boîtier est délégué au GPU (nvJPEG). Cet unpack n'ouvre le RAW
    que pour lire la WB as-shot (métadonnée) et extraire les octets de la miniature.
    """

    asshot_rg: float | None
    asshot_bg: float | None
    jpeg_bytes: bytes | None


def extract_from_open(r) -> EmbeddedExtract:
    """EmbeddedExtract depuis un handle rawpy DÉJÀ ouvert.

    Extrait pour l'unpack unifié du scheduler (revue Fable 5 P-02) : la même
    ouverture rawpy sert au bayer (`gpu_raw.bayer_from_open`) ET au JPEG boîtier.
    """
    import rawpy

    wb = list(r.camera_whitebalance)  # [R, G1, B, G2]
    try:
        thumb = r.extract_thumb()
        jpeg = bytes(thumb.data) if thumb.format == rawpy.ThumbFormat.JPEG else None
    except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
        jpeg = None
    g = wb[1] or 1.0
    return EmbeddedExtract(wb[0] / g, wb[2] / g, jpeg)


def extract_reference(path: str) -> EmbeddedExtract:
    """Ouvre le RAW (CPU) → WB as-shot + **octets** du JPEG boîtier (sans décoder).

    Pendant CPU de `read_raw_reference` pour le pipeline GPU : le demosaic/décodage
    pixel n'est pas fait ici, seulement l'I/O conteneur (irréductible, LibRaw). Le JPEG
    sera décodé par lot sur GPU (`gpu_jpeg`). Picklable (niveau module).
    """
    import rawpy

    try:
        with rawpy.imread(str(path)) as r:
            return extract_from_open(r)
    except Exception:
        return EmbeddedExtract(None, None, None)


def read_raw_references(
    paths: list[str], max_workers: int | None = None
) -> dict[str, RawReference]:
    """Lit en **parallèle borné** les références RAW d'un lot de chemins.

    LibRaw libère le GIL et chaque ouverture est indépendante → scaling quasi-linéaire
    sur les cœurs physiques. `max_workers=None` → borne sûre (`_bounded_workers`, JAMAIS
    32, cf. le gel). Renvoie {chemin: RawReference}. Replie en séquentiel si le pool
    échoue (ex. environnement sans spawn).
    """
    if not paths:
        return {}
    from concurrent.futures import ProcessPoolExecutor

    workers = max_workers if max_workers is not None else _bounded_workers(len(paths))
    try:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(read_raw_reference, paths))
    except Exception:
        results = [read_raw_reference(p) for p in paths]
    return dict(zip(paths, results))
