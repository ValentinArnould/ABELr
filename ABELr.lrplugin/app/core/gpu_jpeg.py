"""JPEG decoding (GPU nvJPEG if available, otherwise CPU libjpeg via torchvision) + render analysis.

Replaces `cv2.imdecode` (pure CPU) everywhere a JPEG is read for analysis: Lr rendered
preview (`Previews.lrdata` / plugin thumbnail) and embedded in-camera JPEG. The device
follows `gpu.device()` (GPU priority, CPU fallback if no CUDA — see `core/gpu.py`):
`decode_jpeg(..., device='cuda')` delegates to nvJPEG and decodes **in batch** (a list of
buffers) to amortize kernel launches = "GPU multithreading"; on CPU, torchvision
decodes via libjpeg-turbo (no real batching, but same API/output contract).

Output: **uint8 CHW RGB tensors on the current device**, ready for
`render_metrics_gpu` (no superfluous CPU round-trip between decoding and measurement when
on GPU). An unreadable JPEG returns `None` at its position (never an exception that kills the
batch) — the caller counts it as "no render".
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
    """Isolates the JPEG stream within a raw buffer.

    Handles both the Lr `.lrfprev` container (header `AgHg`) and the extensionless
    files of `Previews.lrdata`: looks for the SOI marker `FF D8 FF`. None if absent.

    Accepted limitation (Fable 5 review C-05): takes the FIRST SOI — on a
    multi-stream container this would be the smallest level. No effect in practice:
    `previews.find_rendered_preview` already picks the max-level file, the
    multi-level `.lrfprev` is only a fallback.
    """
    if data[:3] == _JPEG_SOI:
        return data
    start = data.find(_JPEG_SOI)
    return data[start:] if start != -1 else None


def _to_uint8_1d(blob: bytes) -> torch.Tensor:
    # bytearray → writable buffer (avoids the frombuffer non-writable warning).
    return torch.frombuffer(bytearray(blob), dtype=torch.uint8)


def decode_blobs(blobs: list[bytes]) -> list[Optional[torch.Tensor]]:
    """Decodes a list of JPEG buffers on the current device (batched nvJPEG if GPU).

    Returns uint8 tensors (3,H,W) on `gpu.device()`, aligned with the input. If
    the batch fails (one unsupported buffer), falls back **per element**: the good
    ones go through, the bad ones become `None`.
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
    """Reads a file (raw JPEG or `.lrfprev`) and decodes it on GPU. None if unreadable."""
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
    """Reads then decodes (GPU, batched) a list of JPEG/`.lrfprev` files."""
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
    """Decodes (GPU) a JPEG buffer and returns the render analysis, or None if unreadable."""
    res = decode_blobs([blob])
    chw = res[0] if res else None
    return analyze_rendered_gpu(chw) if chw is not None else None


def analyze_file(path: str | Path) -> Optional[RenderAnalysis]:
    """Decodes (GPU) a rendered file and returns the analysis, or None if unreadable."""
    chw = decode_file(path)
    return analyze_rendered_gpu(chw) if chw is not None else None
