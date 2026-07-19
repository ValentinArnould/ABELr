"""Embedded camera JPEG — exposure prior (and initial look).

Every ARW contains the JPEG rendered **by the camera body**: it's the exposure
judged correct at capture time + the initial look (Sony Creative Look). sRGB
display-referred. Used as an **exposure-target fallback** when seeds are missing
(user decision: seeds first, camera JPEG second).

Extracted via `raw.load_thumbnail` (LibRaw `extract_thumb`); lightness measured
via `render_metrics.tone_stats` (CIE L*, same metric as the LR render → comparable).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import raw, render_metrics
from .pipeline import RenderAnalysis
from .render_metrics import BandStats, ToneStats

# Hard cap on the number of RAW-reading processes (anti-freeze). Even on a
# beefy machine, beyond this RAM (one spawned interpreter + buffers per process)
# blows up.
_MAX_RAW_WORKERS = 8


def load_embedded_rgb(path: str | Path) -> np.ndarray | None:
    """uint8 sRGB RGB of the embedded camera JPEG, or None if absent/unreadable."""
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
    """Perceived lightness (CIE L*) of the embedded camera JPEG, or None.

    Uses the same `tone_stats` as the LR render → the median L* is directly
    comparable to the exposure target. Serves as a prior when few/no seeds exist.
    """
    rgb = load_embedded_rgb(path)
    if rgb is None:
        return None
    return render_metrics.tone_stats(rgb)


def embedded_target_l(path: str | Path) -> float | None:
    """Median L* of the camera JPEG (fallback exposure target), or None."""
    ts = embedded_tone(path)
    return ts.median_l if ts is not None else None


# --------------------------------------------------------------------------- #
# Combined RAW read (ONE rawpy open) + parallel batch
# --------------------------------------------------------------------------- #
@dataclass
class RawReference:
    """Everything extracted from a photo via ONE RAW open.

    embedded_tone / embedded_bands: sharp-zone measurements of the camera JPEG
                                     (embedded-mode targets; = `sharp.tone`/`sharp.bands`).
    asshot_rg / asshot_bg          : as-shot WB (input to the seeds WB model).
    sharp / glob                   : full analysis (tone+neutral+bands), sharp zone
                                     and global, of the camera JPEG (dual GPU path).
    mask_sharp_frac                : fraction of pixels retained by the sharp mask.
    """

    embedded_tone: ToneStats | None
    embedded_bands: list[BandStats] | None
    asshot_rg: float | None
    asshot_bg: float | None
    sharp: RenderAnalysis | None = None
    glob: RenderAnalysis | None = None
    mask_sharp_frac: float | None = None


def read_raw_reference(path: str | Path) -> RawReference:
    """Opens the RAW **once** → camera JPEG (tone+bands) + as-shot WB.

    Module-level function → picklable for `ProcessPoolExecutor`. The rawpy open
    (~a few seconds/photo) is the dominant cost: doing it once for both uses,
    and parallelizing, is the right perf lever (see `read_raw_references`).
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
    """**Bounded** process count for the batch.

    CRITICAL: `ProcessPoolExecutor(max_workers=None)` used to spawn
    `min(32, cpu+4)` processes, each importing rawpy/numpy/cv2 (Windows spawn)
    + opening a RAW → CPU/RAM saturation = PC freeze. We cap at **physical
    cores** (HT heuristic = logical // 2), capped further, and never more than
    the number of photos.
    """
    logical = os.cpu_count() or 4
    physical = max(1, logical // 2)
    return max(1, min(n_paths, physical, _MAX_RAW_WORKERS))


@dataclass
class EmbeddedExtract:
    """Output of the embedded CPU unpack — **picklable**, JPEG bytes **not decoded**.

    Decoding the camera JPEG is delegated to the GPU (nvJPEG). This unpack only
    opens the RAW to read the as-shot WB (metadata) and extract the thumbnail bytes.
    """

    asshot_rg: float | None
    asshot_bg: float | None
    jpeg_bytes: bytes | None


def extract_from_open(r) -> EmbeddedExtract:
    """EmbeddedExtract from an ALREADY-open rawpy handle.

    Extracted for the scheduler's unified unpack (Fable 5 review P-02): the same
    rawpy open serves both the bayer (`gpu_raw.bayer_from_open`) AND the camera JPEG.
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
    """Opens the RAW (CPU) → as-shot WB + camera JPEG **bytes** (undecoded).

    CPU stage of `read_raw_reference` for the GPU pipeline: pixel demosaic/decode
    is not done here, only the container I/O (irreducible, LibRaw). The JPEG will
    be decoded in a batch on GPU (`gpu_jpeg`). Picklable (module level).
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
    """Reads RAW references for a batch of paths in **bounded parallel**.

    LibRaw releases the GIL and each open is independent → near-linear scaling
    across physical cores. `max_workers=None` → safe bound (`_bounded_workers`,
    NEVER 32, see the freeze note). Returns {path: RawReference}. Falls back to
    sequential if the pool fails (e.g. an environment without spawn support).
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
