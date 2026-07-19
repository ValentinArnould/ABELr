"""Pixel source for analysis: RAW decoding into linear ProPhoto.

Policy (since calibration on a real catalog): **analysis starts from the original
RAW via rawpy.** The Smart Preview was dropped from the analysis path: its DNG
stores camera-native raw (PhotometricInterpretation = LinearRaw, before WB and
before the color matrix), which LibRaw can't decode (JPEG XL tiles) and which a
hand-rolled de-raw-matizer doesn't reproduce faithfully. Details in `previews.py`.

Analysis output: **linear ProPhoto float32 RGB** (wide gamut, no gamma) —
see `core/color` for the choice of color space. A display-referred uint8 sRGB
render is available on demand for display (`LoadedImage.display_u8`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import color, raw


@dataclass
class LoadedImage:
    """Image decoded for analysis + provenance."""

    rgb: np.ndarray   # HxWx3 float32, linear ProPhoto 0-1
    source: str       # "raw" (only current analysis source)
    colorspace: str   # "prophoto_linear"
    width: int
    height: int

    def display_u8(self) -> np.ndarray:
        """uint8 sRGB render for GUI display (out-of-gamut colors clipped)."""
        return color.prophoto_linear_to_srgb_u8(self.rgb)


def load_for_analysis(raw_path: str, half_size: bool = True) -> LoadedImage:
    """Decodes a RAW for analysis (linear ProPhoto).

    `raw_path`: path to the RAW file provided by the plugin. Raises FileNotFoundError
    if missing / empty.
    """
    if not raw_path:
        raise FileNotFoundError("Missing RAW path for analysis.")
    rgb = raw.load_linear(raw_path, half_size=half_size)  # float32 linear ProPhoto
    h, w = rgb.shape[:2]
    return LoadedImage(
        rgb=rgb, source="raw", colorspace="prophoto_linear", width=w, height=h
    )
