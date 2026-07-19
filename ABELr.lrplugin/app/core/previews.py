"""Access to Lightroom previews stored alongside the catalog.

Two bundles, two very different natures:

1. Rendered preview ("Previews.lrdata") — JPEG of the LR render, **settings applied**.
   Display-referred (sRGB/AdobeRGB 8-bit). Used to check the RESULT of a
   correction, **not** to measure which correction to apply. Decoding ~5-20 ms.
   In Lr 13 each pyramid level is a file `{uuid}-{digest}_{size}`
   (raw JPEG, offset 0). A `{uuid}-{digest}.lrfprev` container (header `AgHg`)
   carries the smallest level; the JPEG starts there after the header.

2. Smart Preview ("Smart Previews.lrdata") — lossy DNG **JPEG XL** ~2.5MP.
   Warning: **NOT a usable RGB.** PhotometricInterpretation = 34892
   (LinearRaw): it's demosaiced camera-native raw, **before** white balance
   and **before** the color matrix. Calibration on a real catalog showed that a
   hand-rolled de-raw-matizer doesn't reproduce it faithfully at the level of the
   developed RAW (inconsistent exposure deltas, color bias), and LibRaw can't decode its
   JXL tiles (compression 52546). **Analysis therefore starts from the RAW** (`image_source`),
   not from the Smart Preview. `decode_smart_preview` is still provided for inspection /
   experimentation, but is not used as an analysis source as things stand.

The `uuid` naming these files is NOT `id_global` (what the plugin sends),
but the cache identifier from `previews.db`. `PreviewIndex` bridges the two:
`id_global` → (uuid, digest) → paths.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import numpy as np

from . import catalog
from .catalog import CatalogPaths, preview_subdir

# Level suffix of rendered preview files: "…_2048", "…_320".
_LEVEL_RE = re.compile(r"_(\d+)$")
# Magic bytes at the start of a JPEG stream (Start Of Image).
_JPEG_SOI = b"\xff\xd8\xff"


# --------------------------------------------------------------------------- #
# Rendered preview (Previews.lrdata) — JPEG, LR settings applied
# --------------------------------------------------------------------------- #
def find_rendered_preview(paths: CatalogPaths, uuid: str) -> Path | None:
    """Highest-resolution rendered preview file for the cache `uuid`.

    Searches `{uuid[0]}/{uuid[:4]}/` for all `{uuid}-*` and keeps the
    largest `_{size}` level. Falls back to `.lrfprev` if no numbered
    level is present. `uuid` = preview-uuid (see `PreviewIndex`), not id_global.
    """
    folder = paths.previews / preview_subdir(uuid)
    if not folder.is_dir():
        return None

    best: tuple[int, Path] | None = None
    fallback: Path | None = None
    for f in folder.glob(f"{uuid}-*"):
        m = _LEVEL_RE.search(f.name)
        if m:
            size = int(m.group(1))
            if best is None or size > best[0]:
                best = (size, f)
        elif f.suffix == ".lrfprev":
            fallback = f
    if best is not None:
        return best[1]
    return fallback


def decode_rendered_preview(path: str | Path) -> np.ndarray:
    """Decode a rendered preview file into RGB uint8 (HxWx3).

    Handles both the raw JPEG (offset 0) and the `.lrfprev` container (`AgHg`) by
    locating the SOI marker. Raises ValueError if no JPEG / decoding failed.
    """
    import cv2

    data = Path(path).read_bytes()
    start = 0 if data[:3] == _JPEG_SOI else data.find(_JPEG_SOI)
    if start == -1:
        raise ValueError(f"No JPEG stream in {path}")
    arr = np.frombuffer(data, np.uint8, offset=start)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"JPEG decode failed: {path}")
    return img[:, :, ::-1]  # BGR -> RGB


# --------------------------------------------------------------------------- #
# Smart Preview (Smart Previews.lrdata) — DNG JPEG XL, 16-bit linear
# --------------------------------------------------------------------------- #
def smart_preview_path(paths: CatalogPaths, uuid: str) -> Path | None:
    """Deterministic path of the Smart Preview DNG for the cache `uuid`, or None."""
    p = paths.smart_previews / preview_subdir(uuid) / f"{uuid}.dng"
    return p if p.is_file() else None


def decode_smart_preview(path: str | Path, normalize: bool = False) -> np.ndarray:
    """Decode the Smart Preview's SubIFD (DNG JXL) into uint16.

    Warning: returns **camera-native raw** (LinearRaw, before WB and before the
    color matrix), NOT a displayable or directly analyzable RGB — see the warning
    at the top of the module. Developing it correctly would require applying WB
    (AsShotNeutral), the color matrix (ForwardMatrix), and DNG opcodes.

    Returns the full-resolution SubIFD (~2560 px on the long side). `normalize=True`
    divides by the type's max value (float32 0-1). Requires `tifffile` +
    `imagecodecs` (JPEG XL decoder).
    """
    import tifffile

    with tifffile.TiffFile(str(path)) as tif:
        main = tif.pages[0]
        # The useful image is in the SubIFD (the main page is a YCbCr thumbnail).
        candidates = list(main.pages) or [main]
        page = max(candidates, key=lambda p: p.imagelength * p.imagewidth)
        arr = page.asarray()  # uint16 HxWx3, linear

    if normalize:
        return arr.astype(np.float32) / float(np.iinfo(arr.dtype).max)
    return arr


# --------------------------------------------------------------------------- #
# id_global → preview files resolution (bridges .lrcat + previews.db)
# --------------------------------------------------------------------------- #
class PreviewIndex:
    """Resolves the `id_global` (sent by the plugin) to preview files.

    Opens `.lrcat` and `previews.db` read-only once — designed for
    batch use (500-1000 photos). Close via `close()` or as a context manager.
    """

    def __init__(self, lrcat_path: str | Path) -> None:
        self.paths: CatalogPaths = catalog.resolve_catalog(lrcat_path)
        self._cat: sqlite3.Connection = catalog.open_readonly(self.paths.lrcat)
        self._pv: sqlite3.Connection | None = (
            catalog.open_readonly(self.paths.previews_db)
            if self.paths.previews_db.is_file()
            else None
        )

    def __enter__(self) -> "PreviewIndex":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._cat.close()
        if self._pv is not None:
            self._pv.close()

    def preview_key(self, id_global: str) -> tuple[str, str] | None:
        """(cache uuid, digest) for an `id_global`, or None if there's no preview.

        id_global → id_local (.lrcat) → ImageCacheEntry.uuid/digest (previews.db).
        """
        if self._pv is None:
            return None
        image_id = catalog.resolve_image_id(self._cat, id_global)
        if image_id is None:
            return None
        row = self._pv.execute(
            "SELECT uuid, digest FROM ImageCacheEntry WHERE imageId = ?",
            (image_id,),
        ).fetchone()
        return (row[0], row[1]) if row else None

    # -- Rendered preview (settings applied) -------------------------------- #
    def rendered_path(self, id_global: str) -> Path | None:
        key = self.preview_key(id_global)
        return find_rendered_preview(self.paths, key[0]) if key else None

    def load_rendered(self, id_global: str) -> np.ndarray | None:
        f = self.rendered_path(id_global)
        return decode_rendered_preview(f) if f is not None else None

    # -- Smart Preview (before settings, 16-bit linear) ---------------------- #
    def smart_path(self, id_global: str) -> Path | None:
        key = self.preview_key(id_global)
        return smart_preview_path(self.paths, key[0]) if key else None

    def load_smart(self, id_global: str, normalize: bool = False) -> np.ndarray | None:
        f = self.smart_path(id_global)
        return decode_smart_preview(f, normalize=normalize) if f is not None else None
