"""Selection of the **render** (output-referred) measurement channel.

Three channels, chosen for reliability/speed (user decision):

1. **requestJpegThumbnail** (plugin job `get_thumbnails` / `render_probe`) — fresh Lr
   render, written as JPEG to disk by the plugin. Reflects the current (or probed)
   settings. **Priority** channel; also the only one that lets us PROBE the response.
2. **Previews.lrdata** (`previews.PreviewIndex`) — already-cached rendered preview. Free,
   fast, but may be stale/absent. Passive fallback.
3. **LrExportSession** — full render export, slow/costly. Last resort (not wired
   here; to be enabled on the plugin side if channel 1 turns out to return a stale cache).

This module is **App-side**: it doesn't submit a job itself (that lives in the GUI
workers via the queue). It receives the available inputs (a path to an already-rendered
thumbnail and/or a `PreviewIndex`) and returns the best decoded rendered RGB, ready for `render_metrics`.
Decoding reuses `previews.decode_rendered_preview` (handles raw JPEG and the `.lrfprev` header).
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import numpy as np

from . import previews
from .previews import PreviewIndex


class RenderChannel(str, Enum):
    THUMBNAIL = "thumbnail"   # requestJpegThumbnail (fresh, priority)
    PREVIEW = "preview"       # Previews.lrdata (passive fallback)
    EXPORT = "export"         # LrExportSession (last resort, not wired)
    NONE = "none"             # no render available


def decode_jpeg_file(path: str | Path) -> np.ndarray:
    """Decodes a rendered JPEG (plugin thumbnail or Previews.lrdata file) into RGB uint8.

    Delegates to `previews.decode_rendered_preview`: handles both raw JPEG (offset 0) and
    the `.lrfprev` container (header `AgHg`, looks for the SOI marker).
    """
    return previews.decode_rendered_preview(path)


def resolve_render_path(
    *,
    thumbnail_path: str | Path | None = None,
    preview_index: PreviewIndex | None = None,
    id_global: str | None = None,
) -> tuple[Path | None, RenderChannel]:
    """Locates the render **file** (without decoding) following channel priority.

    Counterpart to `load_rendered` for the **GPU** pipeline: we want the path (to
    read its bytes and decode on GPU via nvJPEG), not a CPU-decoded array.
    Priority: fresh thumbnail (plugin) → Previews.lrdata preview → None.
    """
    if thumbnail_path is not None and Path(thumbnail_path).is_file():
        return Path(thumbnail_path), RenderChannel.THUMBNAIL
    if preview_index is not None and id_global:
        p = preview_index.rendered_path(id_global)
        if p is not None:
            return p, RenderChannel.PREVIEW
    return None, RenderChannel.NONE


def load_rendered(
    *,
    thumbnail_path: str | Path | None = None,
    preview_index: PreviewIndex | None = None,
    id_global: str | None = None,
) -> tuple[np.ndarray | None, RenderChannel]:
    """Returns (rendered RGB uint8, channel used) based on what's available.

    Priority: fresh thumbnail (plugin) → Previews.lrdata preview (passive) → nothing.
    The caller supplies `thumbnail_path` if it already had the plugin render the photo
    (job `get_thumbnails`/`render_probe`), and/or a `PreviewIndex` + `id_global` for
    the passive fallback.
    """
    # 1. Fresh thumbnail written by the plugin (priority channel).
    if thumbnail_path is not None and Path(thumbnail_path).is_file():
        try:
            return decode_jpeg_file(thumbnail_path), RenderChannel.THUMBNAIL
        except ValueError:
            pass  # unreadable file → try the fallback

    # 2. Already-cached rendered preview (passive fallback).
    if preview_index is not None and id_global:
        rgb = preview_index.load_rendered(id_global)
        if rgb is not None:
            return rgb, RenderChannel.PREVIEW

    # 3. LrExportSession: last resort, not wired here.
    return None, RenderChannel.NONE
