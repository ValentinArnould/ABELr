"""Scheduler VRAM-aware — nourrit le GPU sans saturer les 8 Go (combinaison RAM+VRAM).

Politique : décodage pixel **sur GPU**, unpack/I-O conteneur sur CPU **borné**. Deux
étages :

- **Producteur CPU borné** (`ThreadPoolExecutor`, cœurs physiques) : `rawpy` libère le
  GIL → vrai parallélisme pour déballer bayers/octets JPEG en **RAM hôte**. Borné (jamais
  les 32 process qui gelaient le PC).
- **Consommateur GPU en vagues** : traite par **chunks dimensionnés à la VRAM libre**
  (`gpu.vram_budget_bytes`), libère la VRAM entre vagues (`gpu.empty_cache`). Le RAW est
  traité séquentiellement sur GPU (pic VRAM = 1 image, sûr sur 8 Go) pendant que le CPU
  déballe la vague suivante ; le JPEG est décodé par lot (nvJPEG) par vague.

Aucun repli CPU de **calcul** (GPU-strict) : si CUDA manque, `gpu.require_cuda` lève.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

import torch

from . import embedded_jpeg, gpu, gpu_jpeg, gpu_raw, render_metrics_gpu
from .embedded_jpeg import RawReference
from .gpu_raw import RawGpuResult
from .pipeline import RenderAnalysis

Progress = Optional[Callable[[int, int], None]]

# Estimation grossière de la VRAM transitoire par image pleine résolution (~24-33MP) :
# décodage + tampons float du demosaic/Lab. Sert à dimensionner les vagues.
_EST_BYTES_PER_IMG = 33_000_000 * 36


def _cpu_workers() -> int:
    """Borne du pool d'unpack CPU = cœurs physiques (heuristique HT), plafonné."""
    logical = os.cpu_count() or 4
    return max(1, min(logical // 2, 8))


def _wave_size() -> int:
    """Taille de vague GPU = budget VRAM / estimation par image (au moins 1)."""
    try:
        budget = gpu.vram_budget_bytes()
    except Exception:
        return 4
    return max(1, min(16, budget // _EST_BYTES_PER_IMG))


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


# --------------------------------------------------------------------------- #
# RAW : unpack CPU borné (parallèle) → process GPU séquentiel par vague
# --------------------------------------------------------------------------- #
def process_raw_batch(
    paths: list[str], progress: Progress = None
) -> dict[str, Optional[RawGpuResult]]:
    """Décode un lot de RAW sur GPU. {chemin: RawGpuResult|None}."""
    gpu.require_cuda()
    out: dict[str, Optional[RawGpuResult]] = {}
    if not paths:
        return out
    workers = _cpu_workers()
    chunk = max(workers, _wave_size())
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for group in _chunks(paths, chunk):
            bayers = list(ex.map(gpu_raw.unpack_raw, group))  # CPU parallèle borné
            for path, rb in zip(group, bayers):               # GPU séquentiel (VRAM-safe)
                out[path] = gpu_raw.process_bayer_gpu(rb) if rb is not None else None
                done += 1
                if progress:
                    progress(done, len(paths))
            gpu.empty_cache()
    return out


# --------------------------------------------------------------------------- #
# JPEG boîtier embarqué : extract CPU borné → décode + métriques GPU par vague
# --------------------------------------------------------------------------- #
def process_embedded_batch(
    paths: list[str], progress: Progress = None
) -> dict[str, RawReference]:
    """WB as-shot + tone/bandes du JPEG boîtier, décodage **GPU**. {chemin: RawReference}."""
    gpu.require_cuda()
    out: dict[str, RawReference] = {}
    if not paths:
        return out
    workers = _cpu_workers()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        extracts = list(ex.map(embedded_jpeg.extract_reference, paths))  # CPU parallèle

    done = 0
    chunk = max(1, _wave_size())
    for group_paths, group_ex in zip(_chunks(paths, chunk), _chunks(extracts, chunk)):
        blobs = [e.jpeg_bytes for e in group_ex if e.jpeg_bytes is not None]
        blob_pos = [i for i, e in enumerate(group_ex) if e.jpeg_bytes is not None]
        decoded = gpu_jpeg.decode_blobs(blobs) if blobs else []
        dec_by_pos = dict(zip(blob_pos, decoded))
        for i, (path, ex_) in enumerate(zip(group_paths, group_ex)):
            tone = bands = None
            chw = dec_by_pos.get(i)
            if chw is not None:
                ra = render_metrics_gpu.analyze_rendered_gpu(chw)
                tone, bands = ra.tone, ra.bands
            out[path] = RawReference(tone, bands, ex_.asshot_rg, ex_.asshot_bg)
            done += 1
            if progress:
                progress(done, len(paths))
        gpu.empty_cache()
    return out


# --------------------------------------------------------------------------- #
# Aperçu rendu (preview/miniature) : décode + analyse GPU par vague
# --------------------------------------------------------------------------- #
def analyze_render_blobs(
    items: list[tuple[str, bytes]], progress: Progress = None
) -> dict[str, Optional[RenderAnalysis]]:
    """Analyse une liste (clé, octets JPEG rendu) sur GPU. {clé: RenderAnalysis|None}."""
    gpu.require_cuda()
    out: dict[str, Optional[RenderAnalysis]] = {}
    if not items:
        return out
    done = 0
    chunk = max(1, _wave_size())
    for group in _chunks(items, chunk):
        decoded = gpu_jpeg.decode_blobs([blob for _, blob in group])
        for (key, _blob), chw in zip(group, decoded):
            out[key] = render_metrics_gpu.analyze_rendered_gpu(chw) if chw is not None else None
            done += 1
            if progress:
                progress(done, len(items))
        gpu.empty_cache()
    return out
