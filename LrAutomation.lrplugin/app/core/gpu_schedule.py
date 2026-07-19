"""Scheduler VRAM-aware — nourrit le GPU sans saturer les 8 Go (combinaison RAM+VRAM).

Politique : décodage pixel **sur GPU**, unpack/I-O conteneur sur CPU **borné**. Deux
étages, refondus par la revue Fable 5 (G7 : P-01/P-02/P-03/P-06) :

- **Producteur CPU borné** (`ThreadPoolExecutor`, cœurs physiques) : `rawpy` libère le
  GIL → vrai parallélisme pour déballer bayers/octets JPEG en **RAM hôte**. Le pool
  déballe la vague N+1 **pendant** que le GPU traite la vague N (double-buffer, P-01) —
  au plus 2 vagues en vol en RAM.
- **Unpack unifié** (P-02) : un chemin qui manque à la fois côté RAW et côté JPEG
  boîtier n'ouvre le conteneur ARW qu'UNE fois (`_unpack_combined` : bayer + WB
  as-shot + octets du thumb dans le même `with rawpy.imread`).
- **Consommateur GPU en vagues dimensionnées PAR PIPELINE** (P-03) : le RAW pleine
  résolution et les JPEG (~15× plus petits) ont chacun leur estimation de VRAM — les
  vagues nvJPEG passent de 3-5 à 30-60 images. `empty_cache` n'est plus systématique
  (sync + flush allocateur par vague) : réactif sur OOM + hygiène périodique.

Le calcul pixel bascule automatiquement sur CPU si aucun GPU CUDA n'est utilisable
(`gpu.device()` — cf. `core/gpu.py`, politique GPU prioritaire + fallback CPU) : ce
scheduler ne fait aucune hypothèse sur le device, il dimensionne juste les vagues via
`gpu.vram_budget_bytes()` (VRAM réelle si GPU, plafond RAM fixe si CPU). Parité
mesures inchangée à device égal (mêmes kernels, seul l'ordonnancement change) — à
revalider par `tools/validate_gpu_vs_libraw` après tout changement ici (ce script
reste GPU-only, cf. son en-tête).
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

import torch

from . import embedded_jpeg, gpu, gpu_jpeg, gpu_raw, render_metrics_gpu
from .embedded_jpeg import EmbeddedExtract, RawReference
from .gpu_raw import RawBayer, RawGpuResult
from .pipeline import RenderAnalysisDual

Progress = Optional[Callable[[int, int], None]]

# Estimations de VRAM transitoire par image, PAR PIPELINE (revue Fable 5 P-03) :
# - RAW pleine résolution (~24-33 MP) : demosaic + Lab + broadcast des bandes.
# - JPEG décodé (aperçu/boîtier, ~0.5-3 MP) : ~15× plus petit. Une estimation
#   unique taille-RAW donnait des vagues nvJPEG de 3-5 images (batching inutile).
_EST_BYTES_RAW_IMG = 33_000_000 * 36
_EST_BYTES_JPEG_IMG = 80_000_000

# Plafonds de vague (bornent aussi la RAM hôte : 2 vagues en vol avec le prefetch).
_WAVE_CAP_RAW = 16
_WAVE_CAP_JPEG = 64

# Hygiène allocateur : empty_cache toutes les N vagues seulement (P-03 — l'appel
# systématique par vague coûtait un sync + cudaMalloc repayé à la vague suivante).
_EMPTY_CACHE_EVERY = 8


def _cpu_workers() -> int:
    """Borne du pool d'unpack CPU = cœurs physiques (heuristique HT), plafonné."""
    logical = os.cpu_count() or 4
    return max(1, min(logical // 2, 8))


def _wave_size(est_bytes_per_img: int, cap: int) -> int:
    """Taille de vague GPU = budget VRAM / estimation par image (au moins 1)."""
    try:
        budget = gpu.vram_budget_bytes()
    except Exception:
        return 4
    return max(1, min(cap, budget // est_bytes_per_img))


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _maybe_empty_cache(wave_idx: int) -> None:
    if (wave_idx + 1) % _EMPTY_CACHE_EVERY == 0:
        gpu.empty_cache()


def _with_oom_retry(fn, *args):
    """Exécute un pas GPU ; sur OOM, rend la VRAM cachée et retente UNE fois."""
    try:
        return fn(*args)
    except torch.cuda.OutOfMemoryError:
        gpu.empty_cache()
        return fn(*args)


# --------------------------------------------------------------------------- #
# Unpack unifié (P-02) : UNE ouverture rawpy → bayer et/ou référence embedded
# --------------------------------------------------------------------------- #
@dataclass
class _CombinedUnpack:
    bayer: RawBayer | None
    extract: EmbeddedExtract | None


def _unpack_combined(args: tuple[str, bool, bool]) -> _CombinedUnpack:
    """(path, need_bayer, need_jpeg) → bayer et/ou extract, une seule ouverture."""
    path, need_bayer, need_jpeg = args
    import rawpy

    bayer: RawBayer | None = None
    extract: EmbeddedExtract | None = None
    try:
        with rawpy.imread(str(path)) as r:
            if need_bayer:
                bayer = gpu_raw.bayer_from_open(r)
            if need_jpeg:
                extract = embedded_jpeg.extract_from_open(r)
    except Exception:
        pass  # RAW illisible → (None, None), même contrat que unpack_raw/extract_reference
    return _CombinedUnpack(bayer, extract)


# --------------------------------------------------------------------------- #
# Passage combiné RAW + JPEG boîtier (double-buffer CPU/GPU, P-01)
# --------------------------------------------------------------------------- #
def process_combined_batch(
    raw_paths: list[str],
    embedded_paths: list[str],
    progress: Progress = None,
) -> tuple[dict[str, Optional[RawGpuResult]], dict[str, RawReference]]:
    """Décode RAW et/ou JPEG boîtier en un passage.

    Un chemin présent dans les deux listes n'ouvre le conteneur qu'une fois.
    Retourne ({chemin: RawGpuResult|None}, {chemin: RawReference}) — clés limitées
    aux listes demandées. `progress` compte une unité par analyse produite
    (len(raw_paths) + len(embedded_paths) au total).
    """
    raw_out: dict[str, Optional[RawGpuResult]] = {}
    emb_out: dict[str, RawReference] = {}
    need_raw = set(raw_paths)
    need_emb = set(embedded_paths)
    all_paths = list(dict.fromkeys([*raw_paths, *embedded_paths]))
    if not all_paths:
        return raw_out, emb_out

    total = len(raw_paths) + len(embedded_paths)
    done = 0

    def _tick() -> None:
        nonlocal done
        done += 1
        if progress:
            progress(done, total)

    workers = _cpu_workers()
    # Vague dimensionnée pour la charge la plus lourde présente ; au moins la
    # largeur du pool CPU pour que le prefetch occupe tous les workers.
    if need_raw:
        wave = max(workers, _wave_size(_EST_BYTES_RAW_IMG, _WAVE_CAP_RAW))
    else:
        wave = max(workers, _wave_size(_EST_BYTES_JPEG_IMG, _WAVE_CAP_JPEG))
    waves = list(_chunks(all_paths, wave))

    with ThreadPoolExecutor(max_workers=workers) as ex:

        def _submit(wave_paths: list[str]):
            return [
                ex.submit(_unpack_combined, (p, p in need_raw, p in need_emb))
                for p in wave_paths
            ]

        next_futs = _submit(waves[0])
        for wi, wave_paths in enumerate(waves):
            futs = next_futs
            # Double-buffer (P-01) : la vague N+1 se déballe sur le pool CPU
            # pendant que ce thread consomme la vague N sur GPU.
            next_futs = _submit(waves[wi + 1]) if wi + 1 < len(waves) else []

            extracts: list[tuple[str, EmbeddedExtract]] = []
            for path, fut in zip(wave_paths, futs):
                cu = fut.result()
                if path in need_raw:
                    raw_out[path] = (
                        _with_oom_retry(gpu_raw.process_bayer_gpu, cu.bayer)
                        if cu.bayer is not None else None
                    )
                    _tick()
                if path in need_emb:
                    extracts.append(
                        (path, cu.extract or EmbeddedExtract(None, None, None))
                    )

            # JPEG boîtier de la vague : décodage nvJPEG PAR LOT + métriques dual.
            if extracts:
                blobs = [e.jpeg_bytes for _, e in extracts if e.jpeg_bytes]
                pos = [i for i, (_, e) in enumerate(extracts) if e.jpeg_bytes]
                decoded = _with_oom_retry(gpu_jpeg.decode_blobs, blobs) if blobs else []
                dec_by_pos = dict(zip(pos, decoded))
                for i, (path, e) in enumerate(extracts):
                    tone = bands = None
                    sharp = glob = mask_frac = None
                    chw = dec_by_pos.get(i)
                    if chw is not None:
                        dual = _with_oom_retry(
                            render_metrics_gpu.analyze_rendered_gpu_dual, chw
                        )
                        sharp, glob, mask_frac = dual.sharp, dual.glob, dual.mask_sharp_frac
                        tone, bands = sharp.tone, sharp.bands
                    emb_out[path] = RawReference(
                        tone, bands, e.asshot_rg, e.asshot_bg,
                        sharp=sharp, glob=glob, mask_sharp_frac=mask_frac,
                    )
                    _tick()

            _maybe_empty_cache(wi)

    gpu.empty_cache()  # fin de lot : rendre la VRAM mise en cache par l'allocateur
    return raw_out, emb_out


# --------------------------------------------------------------------------- #
# API historiques — wrappers du passage combiné
# --------------------------------------------------------------------------- #
def process_raw_batch(
    paths: list[str], progress: Progress = None
) -> dict[str, Optional[RawGpuResult]]:
    """Décode un lot de RAW sur GPU. {chemin: RawGpuResult|None}."""
    raw_out, _ = process_combined_batch(paths, [], progress=progress)
    return raw_out


def process_embedded_batch(
    paths: list[str], progress: Progress = None
) -> dict[str, RawReference]:
    """WB as-shot + tone/bandes du JPEG boîtier, décodage **GPU**. {chemin: RawReference}."""
    _, emb_out = process_combined_batch([], paths, progress=progress)
    return emb_out


# --------------------------------------------------------------------------- #
# Aperçu rendu (preview/miniature) : décode + analyse GPU par vague
# --------------------------------------------------------------------------- #
def analyze_render_blobs(
    items: list[tuple[str, bytes]], progress: Progress = None
) -> dict[str, Optional[RenderAnalysisDual]]:
    """Analyse une liste (clé, octets JPEG rendu) sur GPU. {clé: RenderAnalysisDual|None}.

    Retourne la paire global + zone nette (le caller utilise `.sharp` pour la mesure
    d'état courant et stocke la paire complète dans le cache)."""
    out: dict[str, Optional[RenderAnalysisDual]] = {}
    if not items:
        return out
    done = 0
    chunk = max(1, _wave_size(_EST_BYTES_JPEG_IMG, _WAVE_CAP_JPEG))
    for wi, group in enumerate(_chunks(items, chunk)):
        decoded = _with_oom_retry(gpu_jpeg.decode_blobs, [blob for _, blob in group])
        for (key, _blob), chw in zip(group, decoded):
            out[key] = (
                _with_oom_retry(render_metrics_gpu.analyze_rendered_gpu_dual, chw)
                if chw is not None else None
            )
            done += 1
            if progress:
                progress(done, len(items))
        _maybe_empty_cache(wi)
    gpu.empty_cache()
    return out
