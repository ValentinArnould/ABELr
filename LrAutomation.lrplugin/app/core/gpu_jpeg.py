"""Décodage JPEG (nvJPEG GPU si dispo, sinon libjpeg CPU via torchvision) + analyse rendu.

Remplace `cv2.imdecode` (CPU pur) partout où on lit un JPEG pour l'analyse : aperçu
rendu Lr (`Previews.lrdata` / miniature plugin) et JPEG boîtier embarqué. Le device
suit `gpu.device()` (GPU prioritaire, repli CPU si aucun CUDA — cf. `core/gpu.py`) :
`decode_jpeg(..., device='cuda')` délègue à nvJPEG et décode **par lot** (une liste de
buffers) pour amortir les lancements kernel = "multithread GPU" ; en CPU, torchvision
décode via libjpeg-turbo (pas de batching réel, mais même API/contrat de sortie).

Sortie : tenseurs **uint8 CHW RGB sur le device courant**, prêts pour
`render_metrics_gpu` (aucun aller-retour CPU superflu entre décodage et mesure quand
GPU). Un JPEG illisible renvoie `None` à sa position (jamais d'exception qui tue le
lot) — le caller le compte comme « sans rendu ».
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import torch
from torchvision.io import ImageReadMode, decode_jpeg

from . import gpu
from .pipeline import RenderAnalysis
from .render_metrics_gpu import analyze_rendered_gpu

_JPEG_SOI = b"\xff\xd8\xff"


def extract_jpeg_stream(data: bytes) -> Optional[bytes]:
    """Isole le flux JPEG dans un buffer brut.

    Gère le conteneur Lr `.lrfprev` (en-tête `AgHg`) et les fichiers sans extension de
    `Previews.lrdata` : on cherche le marqueur SOI `FF D8 FF`. None si absent.

    Limitation assumée (revue Fable 5 C-05) : on prend le PREMIER SOI — sur un
    conteneur multi-flux ce serait le plus petit niveau. Sans effet en pratique :
    `previews.find_rendered_preview` choisit déjà le fichier de niveau max, le
    `.lrfprev` multi-niveaux n'est qu'un repli.
    """
    if data[:3] == _JPEG_SOI:
        return data
    start = data.find(_JPEG_SOI)
    return data[start:] if start != -1 else None


def _to_uint8_1d(blob: bytes) -> torch.Tensor:
    # bytearray → buffer inscriptible (évite l'avertissement frombuffer non-writable).
    return torch.frombuffer(bytearray(blob), dtype=torch.uint8)


def decode_blobs(blobs: list[bytes]) -> list[Optional[torch.Tensor]]:
    """Décode une liste de buffers JPEG sur le device courant (nvJPEG batché si GPU).

    Retourne des tenseurs uint8 (3,H,W) sur `gpu.device()`, alignés sur l'entrée. En
    cas d'échec du lot (un buffer non supporté), repli **par élément** : les bons
    passent, les mauvais deviennent `None`.
    """
    if not blobs:
        return []
    dev = gpu.device()
    tensors = [_to_uint8_1d(b) for b in blobs]
    try:
        out = decode_jpeg(tensors, device=dev, mode=ImageReadMode.RGB)
        return list(out)
    except Exception:
        result: list[Optional[torch.Tensor]] = []
        for t in tensors:
            try:
                result.append(decode_jpeg(t, device=dev, mode=ImageReadMode.RGB))
            except Exception:
                result.append(None)
        return result


def decode_file(path: str | Path) -> Optional[torch.Tensor]:
    """Lit un fichier (JPEG brut ou `.lrfprev`) et le décode sur GPU. None si illisible."""
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError:
        return None
    stream = extract_jpeg_stream(data)
    if stream is None:
        return None
    res = decode_blobs([stream])
    return res[0] if res else None


def decode_files(paths: list[str | Path]) -> list[Optional[torch.Tensor]]:
    """Lit puis décode (GPU, batché) une liste de fichiers JPEG/`.lrfprev`."""
    blobs: list[bytes] = []
    positions: list[int] = []
    out: list[Optional[torch.Tensor]] = [None] * len(paths)
    for i, path in enumerate(paths):
        try:
            stream = extract_jpeg_stream(Path(path).read_bytes())
        except OSError:
            stream = None
        if stream is not None:
            blobs.append(stream)
            positions.append(i)
    for pos, tensor in zip(positions, decode_blobs(blobs)):
        out[pos] = tensor
    return out


def analyze_blob(blob: bytes) -> Optional[RenderAnalysis]:
    """Décode (GPU) un buffer JPEG et renvoie l'analyse rendu, ou None si illisible."""
    res = decode_blobs([blob])
    chw = res[0] if res else None
    return analyze_rendered_gpu(chw) if chw is not None else None


def analyze_file(path: str | Path) -> Optional[RenderAnalysis]:
    """Décode (GPU) un fichier rendu et renvoie l'analyse, ou None si illisible."""
    chw = decode_file(path)
    return analyze_rendered_gpu(chw) if chw is not None else None
